from __future__ import annotations

import asyncio

import asyncpg
import discord
from discord.ext import commands

from strife.commands.admin import AdminCommands
from strife.commands.catalog import CatalogService
from strife.commands.play import register_play
from strife.commands.server_settings import ServerSettingsService
from strife.commands.strife_group import register_strife_group
from strife.config import load_app_config
from strife.engine.registry import GameRegistry
from strife.games.mafia.game import Mafia
from strife.games.tictactoe.game import TicTacToe
from strife.games.test.game import TestGame
from strife.lifecycle.service import LifecycleService
from strife.logging import configure_logging, get_logger
from strife.matchmaking.registries import SessionRegistries
from strife.matchmaking.service import LobbyService, SessionFinalizer
from strife.persistence.migrator import Migrator
from strife.persistence.pool import create_pool
from strife.persistence.repositories import GuildRepository, MatchRepository, MoveRepository, UserRepository
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter
from strife.replay.profile import ProfileService
from strife.replay.service import ReplayService
from strife.routing.cache import InMemoryPayloadCache
from strife.routing.custom_id import CustomIdEncoder
from strife.routing.router import InteractionRouter
from strife.settings import Settings

log = get_logger("bot")


class StrifeBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.pool: asyncpg.Pool | None = None
        self.game_registry: GameRegistry | None = None
        self.sessions: SessionRegistries | None = None
        self.router: InteractionRouter | None = None
        self.emoji: EmojiResolver | None = None
        self.config = None
        self.lifecycle: LifecycleService | None = None
        self._background_tasks: list[asyncio.Task] = []

    async def setup_hook(self) -> None:
        configure_logging(self.settings.log_level)
        self.config = load_app_config(self.settings.config_dir)
        self.pool = await create_pool(self.settings.database_url)
        migrator = Migrator(self.pool, self.settings.migrations_dir)
        applied = await migrator.run()
        if applied:
            log.info("Applied migrations: %s", ", ".join(applied))

        self.game_registry = GameRegistry()
        self.game_registry.register(TicTacToe)
        self.game_registry.register(Mafia)
        self.game_registry.register(TestGame)

        self.emoji = EmojiResolver(self.config.emoji)
        await self.emoji.sync(self)

        cache = InMemoryPayloadCache()
        encoder = CustomIdEncoder(cache)
        compiler = Compiler(self.emoji, encoder)
        self.sessions = SessionRegistries()
        user_errors = UserErrorPresenter(compiler, self.emoji, self.config.text, self.sessions)
        user_success = UserSuccessPresenter(compiler, self.emoji, self.config.text)

        matches = MatchRepository(self.pool)
        moves = MoveRepository(self.pool)
        users = UserRepository(self.pool)
        guilds = GuildRepository(self.pool)
        finalizer = SessionFinalizer(
            registries=self.sessions,
            matches=matches,
            users=users,
            guilds=guilds,
        )

        self.lobby = LobbyService(
            self,
            self.sessions,
            self.game_registry,
            compiler,
            self.config,
            self.emoji,
            finalizer,
        )
        self.lifecycle = LifecycleService(
            self,
            self.sessions,
            self.game_registry,
            self.lobby,
            self.config.text,
            self.config,
            self.emoji,
        )
        self.lobby.lifecycle = self.lifecycle

        self.replay = ReplayService(
            matches, moves, self.game_registry, compiler, self.config.text, user_errors
        )
        self.profile = ProfileService(users, matches, compiler, self.config.text, self.replay)
        self.catalog = CatalogService(self.game_registry, self.config, compiler, self.emoji, self.config.text)
        self.server_settings = ServerSettingsService(
            guilds, compiler, self.emoji, self.config.text, user_errors, user_success
        )

        self.router = InteractionRouter(
            sessions=self.sessions,
            replay=self.replay,
            lobby=self.lobby,
            lifecycle=self.lifecycle,
            profile=self.profile,
            catalog=self.catalog,
            server_settings=self.server_settings,
            encoder=encoder,
            text=self.config.text,
            user_errors=user_errors,
        )

        register_play(self.tree, self.lobby, self.game_registry)
        register_strife_group(
            self.tree,
            lobby=self.lobby,
            lifecycle=self.lifecycle,
            replay=self.replay,
            profile=self.profile,
            catalog=self.catalog,
            server_settings=self.server_settings,
            registry=self.game_registry,
        )
        await self.add_cog(AdminCommands(self, self.settings))
        self.lifecycle.start()
        log.info("Strife subsystems wired")

    async def on_ready(self) -> None:
        log.info("Ready as %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is discord.InteractionType.component and self.router:
            await self.router.dispatch(interaction)

    async def close(self) -> None:
        if self.lifecycle:
            await self.lifecycle.stop()
        if self.pool:
            await self.pool.close()
        await super().close()
