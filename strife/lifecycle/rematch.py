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
from strife.matchmaking.role_validation import invalid_role_reason
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
    session: object | None = None


class RematchManager:
    def __init__(self, registries: SessionRegistries, lobby_service, text: TextConfig) -> None:
        self.registries = registries
        self.lobby = lobby_service
        self.text = text
        self._offers: dict[int, RematchOffer] = {}

    def start_offer(self, thread_id: int, eligible: set[int], match_id: int, outcome: object) -> None:
        session = self.registries.get_game(thread_id)
        self._offers[thread_id] = RematchOffer(
            eligible=set(eligible),
            expires=time.monotonic() + 120,
            match_id=match_id,
            outcome=outcome,
            session=session,
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
        session = offer.session
        if session is None or not hasattr(session, "lobby_surface"):
            return
        lobby_surface = session.lobby_surface
        if lobby_surface is None:
            return
        try:
            results_view = build_results_view(
                game_name=session.game.metadata.name,
                game_key=session.game_key,
                outcome=offer.outcome,
                players=session.players,
                thread_id=thread_id,
                match_id=offer.match_id,
                text=self.text,
                emoji=session.surface.compiler.emoji,
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
            if self._offers.pop(thread_id, None) is not None:
                await self._reset_to_lobby(thread_id, offer)
        else:
            session = offer.session
            if session and hasattr(session, "lobby_surface") and session.lobby_surface is not None:
                expires_at = int(time.time() + max(0, offer.expires - time.monotonic()))
                results_view = build_results_view(
                    game_name=session.game.metadata.name,
                    game_key=session.game_key,
                    outcome=offer.outcome,
                    players=session.players,
                    thread_id=thread_id,
                    match_id=offer.match_id,
                    text=self.text,
                    emoji=session.surface.compiler.emoji,
                    rematch_count=len(offer.votes),
                    rematch_expires_at=expires_at,
                )
                await session.lobby_surface.update(results_view)

    async def _reset_to_lobby(self, thread_id: int, offer: RematchOffer) -> None:
        session = offer.session
        if session is None:
            self._offers.pop(thread_id, None)
            return

        if hasattr(session, "lobby_surface") and session.lobby_surface is not None:
            try:
                results_view = build_results_view(
                    game_name=session.game.metadata.name,
                    game_key=session.game_key,
                    outcome=offer.outcome,
                    players=session.players,
                    thread_id=thread_id,
                    match_id=offer.match_id,
                    text=self.text,
                    emoji=session.surface.compiler.emoji,
                    rematch_count=len(offer.eligible),
                    rematch_disabled=True,
                )
                await session.lobby_surface.update(results_view)
            except Exception:
                log.exception("Failed to disable rematch button after success for thread %s", thread_id)

        game_key = session.game_key
        guild_id = session.guild_id
        members = [
            LobbyMember(user_id=p.user_id, display_name=p.display_name)
            for p in session.players
            if p.user_id and not p.is_bot
        ]
        bots = [
            QueuedBot(name=p.display_name, difficulty=p.bot_difficulty or "medium")
            for p in session.players
            if p.is_bot
        ]

        lobby_id = secrets.randbits(63)
        thread = self.lobby.bot.get_channel(thread_id)
        parent_channel = None
        if isinstance(thread, discord.Thread):
            parent_channel = thread.parent

        target_channel = parent_channel or thread
        if target_channel is None:
            self._offers.pop(thread_id, None)
            return

        lobby = Lobby(
            thread_id=lobby_id,
            guild_id=guild_id,
            channel_id=target_channel.id,
            game_key=game_key,
            creator_id=members[0].user_id if members else 0,
            private=False,
            members=[],
            bots=bots,
            settings=dict(session.settings),
        )
        surface = ViewSurface(self.lobby.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id)
        lobby.surface = surface

        self.registries.active_games.pop(thread_id, None)
        self.registries.add_lobby(lobby)

        for member in members:
            if not await self.registries.reserve_user(
                member.user_id, UserLocation("lobby", lobby_id, guild_id)
            ):
                continue
            lobby.members.append(member)

        meta = self.lobby.registry.metadata(game_key)
        game_cls = self.lobby.registry.get(game_key)
        view = build_lobby_view(
            lobby,
            meta,
            self.lobby.emoji,
            self.text,
            game_cls=game_cls,
            role_invalid_reason=invalid_role_reason(lobby, meta, game_cls),
        )
        await surface.send(target_channel, view)
        lobby.message_id = surface.message_id
        self._offers.pop(thread_id, None)
