from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

import discord

from strife.config.text import TextConfig
from strife.session.errors import SessionError
from strife.lifecycle.results import build_results_view, rematch_eligible
from strife.logging import get_logger
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries, UserLocation
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P

log = get_logger("lifecycle.rematch")


class RematchMemberBusy(SessionError):
    """A rematch member (not the clicker) is already in another lobby or game."""

    def __init__(self, user_id: int, display_name: str) -> None:
        super().__init__("rematch_member_busy")
        self.user_id = user_id
        self.display_name = display_name


@dataclass
class RematchOffer:
    eligible: set[int]
    votes: set[int] = field(default_factory=set)
    expires: float = 0.0
    match_id: int = 0
    outcome: object | None = None
    game_key: str = ""
    game_name: str = ""
    guild_id: int = 0
    channel_id: int = 0
    creator_id: int = 0
    private: bool = False
    settings: dict = field(default_factory=dict)
    members: list[LobbyMember] = field(default_factory=list)
    bots: list[QueuedBot] = field(default_factory=list)
    lobby_surface: object | None = None
    emoji: object | None = None
    result_players: list = field(default_factory=list)
    removed_seats: frozenset[int] = frozenset()
    taken_over_seats: frozenset[int] = frozenset()
    role_keys: dict[int, str | None] = field(default_factory=dict)
    blacklist: set[int] = field(default_factory=set)
    denied: set[int] = field(default_factory=set)


class RematchManager:
    def __init__(
        self, registries: SessionRegistries, lobby_service, text: TextConfig
    ) -> None:
        self.registries = registries
        self.lobby = lobby_service
        self.text = text
        self._offers: dict[int, RematchOffer] = {}
        self._lock = asyncio.Lock()

    def start_offer(self, thread_id: int, match_id: int, outcome: object) -> None:
        session = self.registries.get_game(thread_id)
        if session is None:
            return
        # Players who quit (removed) or went AFK (taken over) don't get a vote
        # or a seat; their seats aren't carried over as bots either.
        removed = frozenset(getattr(session, "_removed_seats", ()))
        taken_over = frozenset(getattr(session, "taken_over", ()))
        members = [
            LobbyMember(user_id=p.user_id, display_name=p.display_name)
            for p in rematch_eligible(session.players, removed, taken_over)
        ]
        bots = [
            QueuedBot(name=p.display_name, difficulty=p.bot_difficulty or "medium")
            for p in session.players
            if p.is_bot and p.seat not in taken_over
        ]
        eligible = {m.user_id for m in members}
        creator_id = getattr(session, "lobby_creator_id", None) or (
            members[0].user_id if members else 0
        )
        if creator_id and not any(m.user_id == creator_id for m in members) and members:
            creator_id = members[0].user_id
        channel_id = int(getattr(session, "lobby_channel_id", 0) or 0)
        if not channel_id:
            bot = getattr(self.lobby, "bot", None)
            channel = (
                bot.get_channel(thread_id)
                if bot is not None and hasattr(bot, "get_channel")
                else None
            )
            if channel is not None and getattr(channel, "parent", None) is not None:
                channel_id = channel.parent.id
            elif channel is not None:
                channel_id = channel.id
        self._offers[thread_id] = RematchOffer(
            eligible=set(eligible),
            expires=time.monotonic() + 120,
            match_id=match_id,
            outcome=outcome,
            game_key=session.game_key,
            game_name=session.game.metadata.name,
            guild_id=session.guild_id,
            channel_id=channel_id,
            creator_id=creator_id,
            private=bool(getattr(session, "lobby_private", False)),
            settings=dict(session.settings),
            members=members,
            bots=bots,
            lobby_surface=getattr(session, "lobby_surface", None),
            emoji=session.surface.compiler.emoji,
            result_players=list(session.players),
            removed_seats=removed,
            taken_over_seats=taken_over,
            role_keys={
                p.seat: session.game.players[p.seat].role_key for p in session.players
            },
            blacklist=set(getattr(session, "lobby_blacklist", ()) or ()),
            denied=set(getattr(session, "lobby_denied", ()) or ()),
        )

    async def expire_stale(self) -> None:
        now = time.monotonic()
        stale: list[tuple[int, RematchOffer]] = []
        async with self._lock:
            for thread_id in list(self._offers):
                offer = self._offers.get(thread_id)
                if offer is None or now <= offer.expires:
                    continue
                if self._offers.pop(thread_id, None) is not None:
                    stale.append((thread_id, offer))
        for thread_id, offer in stale:
            await self._disable_offer(thread_id, offer)

    async def close_all(self) -> None:
        """Shutdown: drop every open offer and disable its button with a restart note."""
        async with self._lock:
            offers = list(self._offers.items())
            self._offers.clear()
        if offers:
            await asyncio.gather(
                *(
                    self._disable_offer(thread_id, offer, restarting=True)
                    for thread_id, offer in offers
                ),
                return_exceptions=True,
            )

    async def _disable_offer(
        self, thread_id: int, offer: RematchOffer, *, restarting: bool = False
    ) -> None:
        lobby_surface = offer.lobby_surface
        if lobby_surface is None:
            return
        try:
            results_view = build_results_view(
                game_name=offer.game_name,
                game_key=offer.game_key,
                outcome=offer.outcome,
                players=offer.result_players,
                removed_seats=offer.removed_seats,
                taken_over_seats=offer.taken_over_seats,
                role_keys=offer.role_keys,
                thread_id=thread_id,
                match_id=offer.match_id,
                text=self.text,
                emoji=offer.emoji,
                rematch_count=len(offer.votes),
                rematch_disabled=True,
                rematch_restarting=restarting,
            )
            await lobby_surface.update(results_view)
        except Exception:
            log.exception("Failed to disable rematch button for thread %s", thread_id)

    def _progress_view(self, thread_id: int, offer: RematchOffer):
        expires_at = int(time.time() + max(0, offer.expires - time.monotonic()))
        return build_results_view(
            game_name=offer.game_name,
            game_key=offer.game_key,
            outcome=offer.outcome,
            players=offer.result_players,
            removed_seats=offer.removed_seats,
            taken_over_seats=offer.taken_over_seats,
            role_keys=offer.role_keys,
            thread_id=thread_id,
            match_id=offer.match_id,
            text=self.text,
            emoji=offer.emoji,
            rematch_count=len(offer.votes),
            rematch_expires_at=expires_at,
        )

    async def vote(self, thread_id: int, user_id: int) -> None:
        launch_offer: RematchOffer | None = None
        expired: RematchOffer | None = None
        progress_view = None
        progress_surface = None
        async with self._lock:
            offer = self._offers.get(thread_id)
            if offer is None:
                raise SessionError("rematch_unavailable")
            if user_id not in offer.eligible:
                raise SessionError("rematch_not_eligible")
            if time.monotonic() > offer.expires:
                if self._offers.pop(thread_id, None) is not None:
                    expired = offer
                else:
                    expired = None
            else:
                expired = None
                offer.votes.add(user_id)
                if offer.votes >= offer.eligible:
                    self._offers.pop(thread_id, None)
                    launch_offer = offer
                elif offer.lobby_surface is not None:
                    progress_view = self._progress_view(thread_id, offer)
                    progress_surface = offer.lobby_surface
        if expired is not None:
            await self._disable_offer(thread_id, expired)
            raise SessionError("rematch_expired")
        if launch_offer is not None:
            try:
                await self._reset_to_lobby(thread_id, launch_offer, voter_id=user_id)
            except SessionError:
                # Keep the offer (and its button) alive so anyone can retry once
                # the blocker clears.
                async with self._lock:
                    restored = thread_id not in self._offers
                    if restored:
                        self._offers[thread_id] = launch_offer
                if restored and launch_offer.lobby_surface is not None:
                    try:
                        await launch_offer.lobby_surface.update(
                            self._progress_view(thread_id, launch_offer)
                        )
                    except Exception:
                        log.exception(
                            "Failed to re-enable rematch button for thread %s",
                            thread_id,
                        )
                raise
            return
        if progress_view is not None and progress_surface is not None:
            await progress_surface.update(progress_view)

    def _busy_error(self, member: LobbyMember, voter_id: int) -> SessionError:
        if member.user_id == voter_id:
            return SessionError("already_in_session")
        return RematchMemberBusy(member.user_id, member.display_name)

    def _closing(self) -> bool:
        return bool(getattr(self.lobby, "_closing", False))

    async def _resolve_parent_channel(
        self, thread_id: int, offer: RematchOffer
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        bot = self.lobby.bot
        if offer.channel_id:
            channel = bot.get_channel(offer.channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(offer.channel_id)
                except discord.NotFound, discord.HTTPException:
                    channel = None
            if channel is not None:
                return channel
        thread = bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound, discord.HTTPException:
                thread = None
        if isinstance(thread, discord.Thread):
            parent = thread.parent
            parent_id = getattr(thread, "parent_id", None)
            if parent is None and parent_id:
                parent = bot.get_channel(parent_id)
                if parent is None:
                    try:
                        parent = await bot.fetch_channel(parent_id)
                    except discord.NotFound, discord.HTTPException:
                        parent = None
            return parent
        return thread

    async def _members_still_in_guild(
        self, offer: RematchOffer, members: list[LobbyMember]
    ) -> list[LobbyMember]:
        bot = self.lobby.bot
        guild = bot.get_guild(offer.guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(offer.guild_id)
            except discord.NotFound, discord.HTTPException:
                return []
        kept: list[LobbyMember] = []
        for member in members:
            found = guild.get_member(member.user_id)
            if found is None:
                try:
                    found = await guild.fetch_member(member.user_id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    raise SessionError("rematch_unavailable") from None
            kept.append(member)
        return kept

    async def _members_who_can_view_channel(
        self,
        offer: RematchOffer,
        members: list[LobbyMember],
        channel,
    ) -> list[LobbyMember]:
        """Keep members who can see ``channel`` (same check as lobby joins)."""
        if not isinstance(channel, (discord.abc.GuildChannel, discord.Thread)):
            return []
        bot = self.lobby.bot
        guild = bot.get_guild(offer.guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(offer.guild_id)
            except discord.NotFound, discord.HTTPException:
                return []
        kept: list[LobbyMember] = []
        for member in members:
            found = guild.get_member(member.user_id)
            if found is None:
                try:
                    found = await guild.fetch_member(member.user_id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    raise SessionError("rematch_unavailable") from None
            if channel.permissions_for(found).view_channel:
                kept.append(member)
        return kept

    async def _reset_to_lobby(
        self, thread_id: int, offer: RematchOffer, *, voter_id: int = 0
    ) -> None:
        if self._closing():
            raise SessionError("rematch_unavailable")
        members = list(offer.members)
        if not members:
            raise SessionError("rematch_unavailable")

        wait = getattr(self.lobby.bot, "wait_until_live_resumed", None)
        if wait is not None:
            await wait()
        if self._closing():
            raise SessionError("rematch_unavailable")

        members = await self._members_still_in_guild(offer, members)
        meta = self.lobby.registry.metadata(offer.game_key)
        if not members or not meta.player_count.is_valid(len(members) + len(offer.bots)):
            raise SessionError("rematch_unavailable")

        target_channel = await self._resolve_parent_channel(thread_id, offer)
        if target_channel is None:
            raise SessionError("rematch_unavailable")
        members = await self._members_who_can_view_channel(
            offer, members, target_channel
        )
        if not members or not meta.player_count.is_valid(len(members) + len(offer.bots)):
            raise SessionError("rematch_unavailable")

        busy = [
            m for m in members if self.registries.location_of(m.user_id) is not None
        ]
        if busy:
            # Prefer reporting the clicker's own lobby/game: they can fix it.
            mine = next((m for m in busy if m.user_id == voter_id), busy[0])
            raise self._busy_error(mine, voter_id)

        if self._closing():
            raise SessionError("rematch_unavailable")

        # Checks passed: disable the button so a second click can't launch twice.
        # vote() re-enables it if anything below fails.
        if offer.lobby_surface is not None:
            try:
                results_view = build_results_view(
                    game_name=offer.game_name,
                    game_key=offer.game_key,
                    outcome=offer.outcome,
                    players=offer.result_players,
                    removed_seats=offer.removed_seats,
                    taken_over_seats=offer.taken_over_seats,
                    role_keys=offer.role_keys,
                    thread_id=thread_id,
                    match_id=offer.match_id,
                    text=self.text,
                    emoji=offer.emoji,
                    rematch_count=len(offer.eligible),
                    rematch_disabled=True,
                )
                await offer.lobby_surface.update(results_view)
            except Exception:
                log.exception(
                    "Failed to disable rematch button after success for thread %s",
                    thread_id,
                )

        if self._closing():
            raise SessionError("rematch_unavailable")

        lobby_id = secrets.randbits(63)
        creator_id = offer.creator_id
        if not any(m.user_id == creator_id for m in members):
            creator_id = members[0].user_id
        approved = {m.user_id for m in members} if offer.private else set()
        lobby = Lobby(
            thread_id=lobby_id,
            guild_id=offer.guild_id,
            channel_id=target_channel.id,
            game_key=offer.game_key,
            creator_id=creator_id,
            private=offer.private,
            members=[],
            bots=list(offer.bots),
            settings=dict(offer.settings),
            approved=approved,
            denied=set(offer.denied),
            blacklist=set(offer.blacklist),
        )
        surface = ViewSurface(
            self.lobby.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id
        )
        lobby.surface = surface
        if self._closing():
            raise SessionError("rematch_unavailable")
        self.registries.add_lobby(lobby)

        reserved: list[int] = []
        try:
            if self._closing():
                raise SessionError("rematch_unavailable")
            for member in members:
                if not await self.registries.reserve_user(
                    member.user_id, UserLocation("lobby", lobby_id, offer.guild_id)
                ):
                    raise self._busy_error(member, voter_id)
                reserved.append(member.user_id)
                lobby.members.append(member)
            if offer.private:
                seated_ids = {m.user_id for m in lobby.members}
                lobby.approved.update(seated_ids)
                lobby.denied.difference_update(seated_ids)
            if self._closing():
                raise SessionError("rematch_unavailable")
        except SessionError:
            for user_id in reserved:
                await self.registries.release_user(user_id)
            self.registries.remove_lobby(lobby_id)
            raise

        view = build_lobby_view(
            lobby,
            meta,
            self.lobby.emoji,
            self.text,
            owner_ids=self.lobby.owner_ids(),
        )
        try:
            await surface.send(target_channel, view)
        except Exception:
            for user_id in reserved:
                await self.registries.release_user(user_id)
            self.registries.remove_lobby(lobby_id)
            log.exception("Failed to post rematch lobby for thread %s", thread_id)
            raise SessionError("rematch_unavailable") from None
        lobby.message_id = surface.message_id
