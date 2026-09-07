# Strife

A thread-based Discord gaming platform (**platform 1.0.0**). Start a lobby with `/play`, play in a public game thread, then rematch, replay, or check your profile.

How players, operators, and game authors are supposed to talk to the bot — and where the current code still drifts — is in [docs/interfaces.md](docs/interfaces.md).

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

Slash **starts** things; the lobby **message** marshals the table; the public **thread** is the match. Full contract: [docs/interfaces.md](docs/interfaces.md).

| Command | What it does |
|---------|----------------|
| `/play` | Start a lobby (`game:` required, `private:` optional) |
| `/strife catalog` | Browse games (Play posts a public lobby in the channel) |
| `/strife profile` | Wins, losses, and recent matches (`user:`, `game:`, `page:` optional) |
| `/strife replay` | Open a finished match by 6-character code |
| `/strife forfeit` | Forfeit a **live** game (leave a lobby with Leave / `/strife lobby leave`) |
| `/strife settings` | Open lobby settings (creator edits; members can view) |
| `/strife server` | Default lobby channel (administrators) |
| `/strife about` | About the project |
| `/strife lobby join/leave/ready` | Join by creator, leave, toggle ready. Other lobby tools are on the lobby message and Settings |

Chess uses `/chess move` with SAN or UCI (`e4`, `Nf3`, `e2e4`) — there are no piece buttons on the board.

Hidden-info games have a **Peek** button on the board. Mafia, Spyfall, and Liar's Dice also DM the role/hand when DMs are open. **Coup is peek-only.** Peek still works if DMs are closed.

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

As an owner (`STRIFE_OWNER_IDS`), type `strife/…` at the **start** of a message in any channel or DM. A mention prefix is not accepted.

| Command | What it does |
|---------|----------------|
| `strife/sync` | Push the slash-command tree (global, `local`, or a guild id) |
| `strife/emoji` | Re-upload application emoji |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |
| `strife/plugins` | List builtin and installed game plugins |
| `strife/install <git-url> [ref]` | Install a game from a git plugin repo |
| `strife/install <key>` | Restore an uninstalled builtin game |
| `strife/update <key> [ref]` | Replace a git plugin's files without deleting match history |
| `strife/uninstall <key> confirm` | Remove a game (builtin or installed) and delete its match history |

To **hide** a game without deleting history, set `enabled: false` in `config/games.yaml`. Uninstall wipes matches and stats for that key. See [docs/plugins.md](docs/plugins.md).

## Write a game

See [docs/interfaces.md](docs/interfaces.md) for the player / operator / author contract, [docs/game-development.md](docs/game-development.md) and [docs/game-api.md](docs/game-api.md) for implementing a game. How games are discovered, isolated from Discord, and exposed in the catalog is in [docs/game-architecture.md](docs/game-architecture.md). Install, update, and uninstall (including wiping history) is in [docs/plugins.md](docs/plugins.md). Each plugin has `plugin.toml` with its own `version`, `platform_version`, and extras. Scaffold an in-tree builtin with `python scripts/scaffold_game.py <key> "Title"`, or copy `templates/game-plugin/` and `strife/install <url>`. Try locally with `python scripts/run_game.py <key>`.

## License

MIT. See [LICENSE](LICENSE). Privacy notes for a public instance are in [docs/privacy.md](docs/privacy.md).
