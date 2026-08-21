# Strife

A thread-based Discord gaming platform. Start a lobby with `/play`, play in a public game thread, then rematch, replay, or check your profile.

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
| `/strife lobby …` | Join, leave, ready, kick, privacy, roles |
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

After the bot is online, in a server where you are listed in `STRIFE_OWNER_IDS`:

- `strife/sync` — register slash commands (set `STRIFE_SYNC_ON_START=true` to sync on every boot)
- `strife/emoji` — upload application emoji from `assets/emoji/`

## Owner commands

Message the bot (or mention it) as an owner:

| Command | What it does |
|---------|----------------|
| `strife/sync` | Push the slash-command tree (global, `local`, or a guild id) |
| `strife/emoji` | Re-upload application emoji |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |

## Write a game

See [docs/game-development.md](docs/game-development.md) and [docs/game-api.md](docs/game-api.md). How games are discovered, isolated from Discord, and exposed in the catalog is in [docs/game-architecture.md](docs/game-architecture.md). Scaffold with `python scripts/scaffold_game.py <key> "Title"` and try it with `python scripts/run_game.py <key>`.

## License

MIT. See [LICENSE](LICENSE). Privacy notes for a public instance are in [docs/privacy.md](docs/privacy.md).
