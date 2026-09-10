# Strife

Discord board games in a public thread. `/play` starts a lobby; the match runs in `{Game} (#ABC123)`. Then rematch, replay, or check your profile.

This is the rewrite of [PlayCord](https://github.com/PlayCord/bot). Platform **1.0.0**.

How slash, lobby, thread, and owner commands are supposed to work: [docs/interfaces.md](docs/interfaces.md).

## Invite

In the [Discord Developer Portal](https://discord.com/developers/applications), create an app, add a bot, and turn on:

- Server Members
- Message Content

Invite with `applications.commands` and `bot`. Permissions:

- View Channels, Send Messages, Send Messages in Threads
- Create Public Threads, Manage Threads
- Embed Links, Attach Files, Use External Emojis
- Add Reactions, Read Message History
- Mention Everyone is **not** needed

Upload application emoji with `strife/emoji` after the first run.

If you publish a public invite, point the app’s privacy-policy URL at [docs/privacy.md](docs/privacy.md) (or a hosted copy).

## Commands

Slash starts things. The lobby message seats the table. The thread is the match.

| Command | What it does |
|---------|----------------|
| `/play` | Start a lobby (`game:` required, `private:` optional) |
| `/strife catalog` | Browse games. Play posts a public lobby in the channel |
| `/strife profile` | Wins, losses, recent matches (`user:`, `game:`, `page:` optional) |
| `/strife replay` | Open a finished match by 6-character code |
| `/strife forfeit` | Forfeit a **live** game (leave a lobby with Leave / `/strife lobby leave`) |
| `/strife settings` | Lobby settings (creator edits; members can view) |
| `/strife server` | Default lobby channel (administrators) |
| `/strife about` | About, attributions, and Changes |
| `/strife lobby join/leave/ready` | Join by creator, leave, ready. Everything else is on the lobby message |

Chess has no piece buttons. Type `/chess move` (`e4`, `Nf3`, `e2e4`).

Hidden-info games have **Peek** on the board. Mafia, Spyfall, and Liar’s Dice also DM the role/hand when DMs work. **Coup is peek-only.**

## Games

Tic-Tac-Toe, Connect Four, Chess, Coup, Mafia, Spyfall, Liar’s Dice.

The API Test game stays in the tree for developers and is disabled by default.

## Self-host

Needs **Python 3.14** and PostgreSQL.

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

Game extras (Chess: `chess`, `resvg-py`) live in each plugin’s `plugin.toml`, not this package. Docker installs shipped extras at image build. A host install pip-installs missing extras on boot unless `STRIFE_SYNC_PLUGIN_DEPS=false`. Compose mounts `./plugins`. `strife/install` needs `git` on PATH (the Docker image has it).

Once the bot is online, in a server where you are in `STRIFE_OWNER_IDS`:

- `strife/sync` — register slash commands (`STRIFE_SYNC_ON_START=true` to sync every boot)
- `strife/emoji` — upload `assets/emoji/` and each plugin’s `emoji/` folder

## Owner commands

Type `strife/…` at the **start** of a message. Mentions are ignored.

| Command | What it does |
|---------|----------------|
| `strife/sync` | Push slash commands (global, `local`, or a guild id) |
| `strife/emoji` | Re-upload application emoji |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |
| `strife/plugins` | List builtin and installed games |
| `strife/install <git-url> [ref]` | Install a game from git |
| `strife/install <key>` | Restore an uninstalled builtin |
| `strife/update <key> [ref]` | Replace a git plugin’s files; keep match history |
| `strife/uninstall <key> confirm` | Remove a game and delete its match history |

To hide a game without deleting history, set `enabled: false` in `config/games.yaml`. Uninstall wipes matches and stats. See [docs/plugins.md](docs/plugins.md).

## Write a game

| Doc | For |
|-----|-----|
| [game-development.md](docs/game-development.md) | Tutorial |
| [game-api.md](docs/game-api.md) | Methods and move log |
| [plugins.md](docs/plugins.md) | Install / update / uninstall |
| [game-architecture.md](docs/game-architecture.md) | How the host loads a game |

```bash
python scripts/scaffold_game.py my_game "My Game"
python scripts/run_game.py my_game
```

Third-party games: GitHub template in `templates/game-plugin/`, then `strife/install <url>`. Each plugin has `plugin.toml` and `changelog.toml` (shown in `/strife about` → Changes).

## License

MIT. See [LICENSE](LICENSE). Public-instance privacy notes: [docs/privacy.md](docs/privacy.md).
