from __future__ import annotations

from collections import OrderedDict

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchRepository, MoveRepository
from strife.presentation.compiler import Compiler
from strife.presentation.message import send_ephemeral_error
from strife.replay.simulator import ReplaySimulator
from strife.replay.view import build_replay_view


class ReplayService:
    def __init__(
        self,
        matches: MatchRepository,
        moves: MoveRepository,
        simulator: ReplaySimulator,
        compiler: Compiler,
        text: TextConfig,
    ) -> None:
        self.matches = matches
        self.moves = moves
        self.simulator = simulator
        self.compiler = compiler
        self.text = text
        self._cache: OrderedDict[int, list] = OrderedDict()
        self._cache_size = 64

    async def _frames(self, match_id: int):
        if match_id in self._cache:
            self._cache.move_to_end(match_id)
            return self._cache[match_id]
        detail = await self.matches.get(match_id)
        if detail is None:
            return None
        move_records = await self.moves.list_for_match(match_id)
        frames = await self.simulator.simulate(detail, move_records)
        self._cache[match_id] = frames
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return frames

    async def open(self, interaction: discord.Interaction, match_ref: str | int) -> None:
        detail = await self.matches.get(match_ref)
        if detail is None:
            await send_ephemeral_error(interaction, self.text.get("common.match_not_found"))
            return
        frames = await self._frames(detail.id)
        if not frames:
            await send_ephemeral_error(interaction, self.text.get("common.replay_unavailable"))
            return
        game_name = self.simulator._registry.metadata(detail.game_key).name
        view = build_replay_view(
            detail,
            0,
            len(frames),
            owner_id=interaction.user.id,
            frame_view=frames[0].view,
            text=self.text,
            game_name=game_name,
            emoji=self.compiler._emoji,
        )
        compiled = self.compiler.compile(view, resource_id=detail.id, prefix="r_nav:")
        await interaction.response.send_message(view=compiled)

    async def render_frame(
        self,
        match_id: int,
        frame: int,
        interaction: discord.Interaction,
        *,
        owner_id: int,
        seek: bool = False,
    ) -> None:
        detail = await self.matches.get(match_id)
        if detail is None:
            await send_ephemeral_error(interaction, self.text.get("common.match_not_found"))
            return
        frames = await self._frames(match_id)
        if not frames:
            await send_ephemeral_error(interaction, self.text.get("common.replay_unavailable"))
            return
        frame = max(0, min(frame, len(frames) - 1))
        game_name = self.simulator._registry.metadata(detail.game_key).name
        view = build_replay_view(
            detail,
            frame,
            len(frames),
            owner_id=owner_id,
            frame_view=frames[frame].view,
            text=self.text,
            game_name=game_name,
            emoji=self.compiler._emoji,
        )
        compiled = self.compiler.compile(view, resource_id=match_id, prefix="r_nav:")
        if interaction.response.is_done():
            await interaction.edit_original_response(view=compiled)
        else:
            await interaction.response.edit_message(view=compiled)
