# Strife

A thread-based Discord gaming platform (**platform 1.0.0**). Start a lobby with `/play`, play in a public game thread, then rematch, replay, or check your profile.

This is the rewrite of [PlayCord](https://github.com/PlayCord/bot).

## Invite / permissions

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and enable these **Privileged Gateway Intents**:

- Server Members
- Message Content

Invite the bot with `applications.commands` and `bot`. Needed bot permissions:

- View Channels, Send Messages, Send Messages in Threads
- Create Public Threads, Manage Threads
- Embed Links, Attach Files, Use External Emojis
- Add Reactions, Read Message History
- Mention Everyone is **not** required

Application (bot) emoji are uploaded with `strife/emoji` after the first run.

If you publish a public invite, set the application's privacy-policy URL to `docs/privacy.md` on your hosting site (or the copy in this repo).

## Commands

| Command | What it does |
|---------|----------------|
| `/play` | Open a lobby for a game |
| `/strife catalog` | Browse the launch catalog |
| `/strife profile` | Wins, losses, and recent matches |
| `/strife replay` | Open a finished match by code |
| `/strife forfeit` | Leave a lobby or forfeit a live game |
| `/strife settings` | Lobby settings |
| `/strife server` | Default lobby channel (administrators) |
| `/strife about` | About the project |
| `/strife lobby …` | Join, leave, ready, kick, privacy |
| `/strife bot add/remove` | Fill empty seats |

Chess uses `/chess move` with SAN or UCI (`e4`, `Nf3`, `e2e4`).

Hidden-info games (Mafia, Spyfall, Liar's Dice, Coup) also have a **Peek** button on the board. Enable DMs from server members if you want the role/hand sent privately; peek still works if DMs are closed.

## Launch games

Tic-Tac-Toe, Connect Four, Chess, Coup, Mafia, Spyfall, Liar's Dice.

The API Test game stays in the tree for developers and is disabled by default.

## Self-host

Requires **Python 3.14** and PostgreSQL.

```bash
cp .env.example .env
# set STRIFE_DISCORD_TOKEN and STRIFE_OWNER_IDS

docker compose up --build
```

Or run the bot on the host against local Postgres:

```bash
python -m pip install -e .
python -m strife
```

Game extras (Chess: `chess`, `resvg-py`) are declared in each plugin's `plugin.toml`, not in this package. Docker installs shipped extras at image build; a host install pip-installs missing extras on boot unless `STRIFE_SYNC_PLUGIN_DEPS=false`. Compose mounts `./plugins` for git-installed games. `strife/install <git-url>` needs `git` on PATH (the Docker image already has it).

After the bot is online, in a server where you are listed in `STRIFE_OWNER_IDS`:

- `strife/sync` — register slash commands (set `STRIFE_SYNC_ON_START=true` to sync on every boot)
- `strife/emoji` — upload application emoji from `assets/emoji/` (platform set) and each plugin's `emoji/` folder

## Owner commands

Message the bot (or mention it) as an owner:

| Command | What it does |
|---------|----------------|
| `strife/sync` | Push the slash-command tree (global, `local`, or a guild id) |
| `strife/emoji` | Re-upload application emoji |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |
| `strife/plugins` | List builtin and installed game plugins |
| `strife/install <git-url>` | Install a game from a GitHub (or other git) plugin repo |
| `strife/install <key>` | Restore an uninstalled builtin game |
| `strife/update <key> [ref]` | Replace a git plugin's files without deleting match history |
| `strife/uninstall <key> confirm` | Remove a game (builtin or installed) and delete its match history |

## Write a game

See [docs/game-development.md](docs/game-development.md) and [docs/game-api.md](docs/game-api.md). How games are discovered, isolated from Discord, and exposed in the catalog is in [docs/game-architecture.md](docs/game-architecture.md). Install, update, and uninstall (including wiping history) is in [docs/plugins.md](docs/plugins.md). Each plugin has `plugin.toml` with its own `version`, `platform_version`, and extras. Scaffold an in-tree builtin with `python scripts/scaffold_game.py <key> "Title"`, or copy `templates/game-plugin/` and `strife/install <url>`. Try locally with `python scripts/run_game.py <key>`.

## License

MIT. See [LICENSE](LICENSE). Privacy notes for a public instance are in [docs/privacy.md](docs/privacy.md).
