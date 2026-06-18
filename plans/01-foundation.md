# 01 - Foundation

Project skeleton, dependency declaration, settings, logging, the discord.py client, and the process lifecycle. Everything else depends on this. See [00-overview.md](00-overview.md) for global decisions.

---

## 1. Goals

- Bootable bot process that connects to Discord and exposes a `setup_hook` where every subsystem is wired.
- Typed, validated configuration sourced from `.env` (secrets) + on-disk config files (behavior).
- Structured logging usable from any module.
- Clean, ordered startup and graceful shutdown (DB pool, background tasks, gateway).

---

## 2. `pyproject.toml`

Use a PEP 621 layout with a src-less package (`strife/` at repo root).

```toml
[project]
name = "strife"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
  "discord.py>=2.6",
  "asyncpg>=0.30",
  "msgpack>=1.1",
  "PyYAML>=6.0",
  "pydantic>=2",
  "pydantic-settings>=2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[project.scripts]
strife = "strife.__main__:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Implementation note: confirm `discord.py>=2.6` and `aiohttp` install cleanly on 3.14 (see risk note in [00-overview.md](00-overview.md)). If not, set `requires-python = ">=3.13"` and recreate the venv.

---

## 3. Settings - `strife/settings.py`

A `pydantic-settings` model reads `.env`. Secrets and environment-specific values only; behavioral config lives in the YAML/TOML files (see [02-configuration-and-emoji.md](02-configuration-and-emoji.md)).

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STRIFE_")

    discord_token: str
    database_url: str                 # postgresql://user:pass@host:5432/strife
    home_guild_id: int                # guild whose application emojis are managed
    owner_ids: tuple[int, ...] = ()   # privileged owners for strife/* commands
    log_level: str = "INFO"
    config_dir: Path = Path("config")
    migrations_dir: Path = Path("migrations")

def get_settings() -> Settings: ...   # cached singleton (functools.lru_cache)
```

`.env.example` documents: `STRIFE_DISCORD_TOKEN`, `STRIFE_DATABASE_URL`, `STRIFE_HOME_GUILD_ID`, `STRIFE_OWNER_IDS` (comma-separated), `STRIFE_LOG_LEVEL`.

Note: `owner_ids` is the authoritative privileged list (Architecture's "static owner list"); it lives in `.env`/environment rather than a committed file so it is deployment-specific.

---

## 4. Logging - `strife/logging.py`

- `configure_logging(level)` installs a single root handler with a concise structured format: timestamp, level, logger name, message, and optional `match_id` / `guild_id` context.
- Hand discord.py's own logger our handler (do not let it set up its own).
- Provide `get_logger(name)` thin wrapper so modules use a consistent namespace (`strife.<subsystem>`).

---

## 5. Bot client - `strife/bot.py`

Subclass `commands.Bot` (gives both the app-command tree and the `on_message` listener needed for owner admin commands).

```python
class StrifeBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True   # required for strife/* admin message-commands
        intents.guilds = True
        intents.members = True           # resolving member display names / mentions
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        # Subsystem singletons (assigned in setup_hook):
        self.pool: asyncpg.Pool
        self.game_registry: GameRegistry
        self.sessions: SessionRegistries
        self.router: InteractionRouter
        self.emoji: EmojiResolver
        self.config: AppConfig

    async def setup_hook(self) -> None:
        # Ordered wiring - see Section 6.
        ...
```

Intents rationale: `message_content` is mandatory because owner admin commands arrive as plain messages prefixed `strife/` (Architecture section 5); `members` supports resolving player mentions in lobby/roster views.

---

## 6. Startup sequence (`setup_hook`)

Order matters; each step depends on the previous.

```mermaid
flowchart TD
    s1["load Settings"] --> s2["configure logging"]
    s2 --> s3["load AppConfig (games.yaml, text.toml, emoji.yaml)"]
    s3 --> s4["create asyncpg pool"]
    s4 --> s5["run migrations"]
    s5 --> s6["build GameRegistry (register tictactoe, mafia)"]
    s6 --> s7["resolve + sync emoji"]
    s7 --> s8["init SessionRegistries + InteractionRouter"]
    s8 --> s9["register app commands + add admin on_message listener"]
    s9 --> s10["start background tasks (AFK scheduler)"]
```

1. `get_settings()`.
2. `configure_logging(settings.log_level)`.
3. Load config files into an `AppConfig` ([02](02-configuration-and-emoji.md)).
4. `create_pool(settings.database_url)` ([03](03-persistence.md)).
5. `Migrator(pool, migrations_dir).run()`.
6. Instantiate game plugins and populate `GameRegistry` ([06](06-game-engine-api.md)).
7. `EmojiResolver` syncs application emojis against `emoji.yaml` ([02](02-configuration-and-emoji.md)).
8. Construct `SessionRegistries` ([07](07-matchmaking-and-lobby.md)) and `InteractionRouter` ([05](05-interaction-routing.md)).
9. Register slash commands and add the admin `on_message` listener ([09](09-commands.md)). Do **not** auto-sync the command tree on startup; syncing is manual via `strife/sync` to respect Discord rate limits.
10. Start the AFK/timeout background task ([08](08-session-lifecycle.md)).

Interaction dispatch: override `on_interaction` (or attach a listener) to forward component interactions to `self.router`. App-command interactions are still handled by the tree.

---

## 7. Entrypoint - `strife/__main__.py`

```python
def main() -> None:
    settings = get_settings()
    bot = StrifeBot(settings)
    bot.run(settings.discord_token, log_handler=None)  # we own logging

if __name__ == "__main__":
    main()
```

Runnable as `python -m strife` or via the `strife` console script.

---

## 8. Graceful shutdown

Override `close()` to tear down in reverse order: cancel background tasks, then `await self.pool.close()`, then `await super().close()`. Because of Decision D4 (no live-state persistence), in-flight sessions are intentionally dropped on shutdown; no state flush is attempted.

---

## 9. Deliverables checklist

- [ ] `pyproject.toml` with runtime + dev deps and pytest config.
- [ ] `.env.example` enumerating required `STRIFE_*` keys.
- [ ] `strife/settings.py` (`Settings`, `get_settings`).
- [ ] `strife/logging.py` (`configure_logging`, `get_logger`).
- [ ] `strife/bot.py` (`StrifeBot` with intents + `setup_hook` skeleton calling each subsystem).
- [ ] `strife/__main__.py` (`main`).
- [ ] Verified boot: process connects to the gateway and logs "ready" with zero registered subsystems failing.
