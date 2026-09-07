from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

import discord

from strife.config.text import TextConfig
from strife.engine.errors import SessionError
from strife.lifecycle.results import build_results_view
from strife.logging import get_logger
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries, UserLocation
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P

log = get_logger("lifecycle.rematch")


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


class RematchManager:
    def __init__(self, registries: SessionRegistries, lobby_service, text: TextConfig) -> None:
        self.registries = registries
        self.lobby = lobby_service
        self.text = text
        self._offers: dict[int, RematchOffer] = {}

    def start_offer(self, thread_id: int, eligible: set[int], match_id: int, outcome: object) -> None:
        session = self.registries.get_game(thread_id)
        if session is None:
            return
        members = [
            LobbyMember(user_id=p.user_id, display_name=p.display_name)
            for p in session.players
            if p.user_id and not p.is_bot and not p.taken_over
        ]
        bots = [
            QueuedBot(name=p.display_name, difficulty=p.bot_difficulty or "medium")
            for p in session.players
            if p.is_bot
        ]
        creator_id = getattr(session, "lobby_creator_id", None) or (
            members[0].user_id if members else 0
        )
        if creator_id and not any(m.user_id == creator_id for m in members) and members:
            creator_id = members[0].user_id
        thread = getattr(self.lobby, "bot", None)
        channel_id = 0
        if thread is not None:
            channel = thread.get_channel(thread_id) if hasattr(thread, "get_channel") else None
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
        )

    async def expire_stale(self) -> None:
        now = time.monotonic()
        for thread_id in list(self._offers):
            offer = self._offers.get(thread_id)
            if offer is None or now <= offer.expires:
                continue
            if self._offers.pop(thread_id, None) is not None:
                await self._disable_offer(thread_id, offer)

    async def _disable_offer(self, thread_id: int, offer: RematchOffer) -> None:
        lobby_surface = offer.lobby_surface
        if lobby_surface is None:
            return
        try:
            results_view = build_results_view(
                game_name=offer.game_name,
                game_key=offer.game_key,
                outcome=offer.outcome,
                players=offer.result_players,
                thread_id=thread_id,
                match_id=offer.match_id,
                text=self.text,
                emoji=offer.emoji,
                rematch_count=len(offer.votes),
                rematch_disabled=True,
            )
            await lobby_surface.update(results_view)
        except Exception:
            log.exception("Failed to disable rematch button for thread %s", thread_id)

    async def vote(self, thread_id: int, user_id: int) -> None:
        offer = self._offers.get(thread_id)
        if offer is None:
            raise SessionError("rematch_unavailable")
        if user_id not in offer.eligible:
            raise SessionError("rematch_not_eligible")
        if time.monotonic() > offer.expires:
            if self._offers.pop(thread_id, None) is not None:
                await self._disable_offer(thread_id, offer)
            raise SessionError("rematch_expired")
        offer.votes.add(user_id)
        if offer.votes >= offer.eligible:
            self._offers.pop(thread_id, None)
            try:
                await self._reset_to_lobby(thread_id, offer)
            except SessionError:
                self._offers[thread_id] = offer
                raise
        elif offer.lobby_surface is not None:
            expires_at = int(time.time() + max(0, offer.expires - time.monotonic()))
            results_view = build_results_view(
                game_name=offer.game_name,
                game_key=offer.game_key,
                outcome=offer.outcome,
                players=offer.result_players,
                thread_id=thread_id,
                match_id=offer.match_id,
                text=self.text,
                emoji=offer.emoji,
                rematch_count=len(offer.votes),
                rematch_expires_at=expires_at,
            )
            await offer.lobby_surface.update(results_view)

    async def _reset_to_lobby(self, thread_id: int, offer: RematchOffer) -> None:
        if offer.lobby_surface is not None:
            try:
                results_view = build_results_view(
                    game_name=offer.game_name,
                    game_key=offer.game_key,
                    outcome=offer.outcome,
                    players=offer.result_players,
                    thread_id=thread_id,
                    match_id=offer.match_id,
                    text=self.text,
                    emoji=offer.emoji,
                    rematch_count=len(offer.eligible),
                    rematch_disabled=True,
                )
                await offer.lobby_surface.update(results_view)
            except Exception:
                log.exception("Failed to disable rematch button after success for thread %s", thread_id)

        members = list(offer.members)
        if not members:
            raise SessionError("rematch_unavailable")

        busy = [
            m
            for m in members
            if self.registries.location_of(m.user_id) is not None
        ]
        if busy:
            raise SessionError("already_in_session")

        thread = self.lobby.bot.get_channel(thread_id)
        parent_channel = None
        if isinstance(thread, discord.Thread):
            parent_channel = thread.parent
        target_channel = parent_channel or thread
        if target_channel is None and offer.channel_id:
            target_channel = self.lobby.bot.get_channel(offer.channel_id)
        if target_channel is None:
            raise SessionError("rematch_unavailable")

        lobby_id = secrets.randbits(63)
        creator_id = offer.creator_id
        if not any(m.user_id == creator_id for m in members):
            creator_id = members[0].user_id
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
        )
        surface = ViewSurface(self.lobby.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id)
        lobby.surface = surface
        self.registries.add_lobby(lobby)

        reserved: list[int] = []
        try:
            for member in members:
                if not await self.registries.reserve_user(
                    member.user_id, UserLocation("lobby", lobby_id, offer.guild_id)
                ):
                    raise SessionError("already_in_session")
                reserved.append(member.user_id)
                lobby.members.append(member)
        except SessionError:
            for user_id in reserved:
                await self.registries.release_user(user_id)
            self.registries.remove_lobby(lobby_id)
            raise

        meta = self.lobby.registry.metadata(offer.game_key)
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
