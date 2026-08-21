from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from strife.config import AppConfig
from strife.engine.errors import SessionError
from strife.engine.metadata import GameMetadata
from strife.engine.registry import GameRegistry
from strife.logging import get_logger
from strife.matchmaking.finalizer import SessionFinalizer
from strife.matchmaking.lobby import Lobby
from strife.matchmaking.lobby_commands import LobbyCommandsMixin
from strife.matchmaking.lobby_controls import LobbyControlsMixin
from strife.matchmaking.lobby_flow import LobbyFlowMixin, NeedTextChannel
from strife.matchmaking.lobby_moderation import LobbyModerationMixin
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries
from strife.matchmaking.role_validation import clear_ready_if_roles_invalid, invalid_role_reason
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.presentation.user_error import ErrorContext, UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter

if TYPE_CHECKING:
    from strife.engine.game import Game

log = get_logger("matchmaking.service")

__all__ = ["LobbyService", "NeedTextChannel", "SessionFinalizer"]


class LobbyService(
    LobbyFlowMixin,
    LobbyControlsMixin,
    LobbyModerationMixin,
    LobbyCommandsMixin,
):
    def __init__(
        self,
        bot: discord.Client,
        registries: SessionRegistries,
        registry: GameRegistry,
        compiler: Compiler,
        config: AppConfig,
        emoji: EmojiResolver,
        finalizer: SessionFinalizer,
        lifecycle=None,
        user_errors: UserErrorPresenter | None = None,
        user_success: UserSuccessPresenter | None = None,
    ) -> None:
        self.bot = bot
        self.registries = registries
        self.registry = registry
        self.compiler = compiler
        self.config = config
        self.emoji = emoji
        self.text = config.text
        self.finalizer = finalizer
        self.lifecycle = lifecycle
        self.user_errors = user_errors or UserErrorPresenter(compiler, emoji, config.text, registries)
        self.user_success = user_success or UserSuccessPresenter(compiler, emoji, config.text)


    def _error_ctx(
        self,
        interaction: discord.Interaction,
        *,
        lobby: Lobby | None = None,
        user_id: int | None = None,
        reason_key: str | None = None,
        reason_kwargs: dict | None = None,
    ) -> ErrorContext:
        uid = user_id if user_id is not None else interaction.user.id
        loc = self.registries.location_of(uid)
        return ErrorContext(
            interaction=interaction,
            user_id=uid,
            lobby=lobby,
            location=loc,
            reason_key=reason_key,
            reason_kwargs=reason_kwargs,
        )


    async def _error(
        self,
        interaction: discord.Interaction,
        code: str,
        *,
        lobby: Lobby | None = None,
        user_id: int | None = None,
        reason_key: str | None = None,
        reason_kwargs: dict | None = None,
    ) -> None:
        await self.user_errors.send(
            interaction,
            code,
            context=self._error_ctx(
                interaction,
                lobby=lobby,
                user_id=user_id,
                reason_key=reason_key,
                reason_kwargs=reason_kwargs,
            ),
        )


    async def _display_name(self, guild: discord.Guild | None, user_id: int) -> str:
        if guild is None:
            return f"User {user_id}"
        member = guild.get_member(user_id)
        if member is not None:
            return member.display_name
        try:
            fetched = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            return f"User {user_id}"
        return fetched.display_name


    def _meta(self, game_key: str) -> GameMetadata:
        try:
            return self.registry.metadata(game_key)
        except KeyError:
            raise SessionError("unknown_game") from None


    def _default_settings(self, meta: GameMetadata) -> dict:
        settings = {}
        yaml_overrides = self.config.games.for_game(meta.key).settings_overrides
        for option in meta.settings:
            settings[option.key] = yaml_overrides.get(option.key, option.default)
        return settings


    def _is_lobby_member(self, lobby: Lobby, user_id: int) -> bool:
        return any(member.user_id == user_id for member in lobby.members)


    def _game_cls(self, game_key: str) -> type[Game] | None:
        try:
            return self.registry.get(game_key)
        except KeyError:
            return None


    def _role_invalid_reason(self, lobby: Lobby, meta: GameMetadata) -> str | None:
        return invalid_role_reason(lobby, meta, self._game_cls(lobby.game_key))


    def _clear_ready_if_roles_invalid(self, lobby: Lobby, meta: GameMetadata) -> None:
        clear_ready_if_roles_invalid(lobby, meta, self._game_cls(lobby.game_key))


    def _build_lobby_view(self, lobby: Lobby, meta: GameMetadata):
        return build_lobby_view(
            lobby,
            meta,
            self.emoji,
            self.text,
            game_cls=self._game_cls(lobby.game_key),
            role_invalid_reason=self._role_invalid_reason(lobby, meta),
        )


    async def _eject_member(self, lobby: Lobby, user_id: int) -> bool:
        if not any(member.user_id == user_id for member in lobby.members):
            return False
        lobby.members = [member for member in lobby.members if member.user_id != user_id]
        lobby.ready.discard(user_id)
        lobby.role_selection.pop(user_id, None)
        await self.registries.release_user(user_id)
        return True


    async def _success(
        self,
        interaction: discord.Interaction,
        code: str,
        **format_kwargs: object,
    ) -> None:
        await self.user_success.send(
            interaction,
            code,
            format_kwargs=format_kwargs or None,
        )


    async def _disable_and_report_closed(
        self, interaction: discord.Interaction, message_key: str
    ) -> None:
        if interaction.message:
            try:
                view = discord.ui.LayoutView.from_message(interaction.message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True

                if not interaction.response.is_done():
                    await interaction.response.edit_message(view=view)
                else:
                    await interaction.message.edit(view=view)

                await self._error(interaction, message_key)
                return
            except Exception as exc:
                log.warning(
                    "Failed to disable components on closed lobby message: %s", exc
                )

        await self._error(interaction, message_key)


