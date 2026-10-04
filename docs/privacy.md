# Privacy

Strife is a Discord bot. If you invite the public instance, this is what it stores.

## Stored

- Guild (server) IDs and an optional default lobby channel
- User IDs and display names of people who play
- Live matches from the start (`matches` row with status `live`): game, guild / thread / message IDs, seed, settings, game version and build, timeout settings, lobby privacy and creator, and each seat’s user ID, display name, role, and bot flags. Moves are appended as they happen
- Finished matches: the same row (status `completed` or `abandoned`), outcome, and the full move log (for replay)
- Per-user win / loss / draw / played counts by game

Lobbies stay in the memory of the program and are not stored.

## Not stored

- Channel messages (except the IDs of the match thread, board, header, and lobby card)
- Discord account fields other than user ID and the display name recorded at play time

## Why

So `/strife replay` and `/strife profile` work, and so a restart can resume a live match. Guild IDs so `/strife server` can remember the default lobby channel.

## Who can see what

- Every match is tied to the server it was played in.
- `/strife profile` shows a player's wins, losses, draws, and recent matches **from the current server only**. Anyone in that server can look up any player, but games from other servers never show up.
- `/strife replay` only opens a match by its 6-character code, and only if it was played in the server where you run the command. Anyone in that server who has the code can watch the replay, including the display names, roles, and moves recorded in it.
- Results posted in a game thread are visible to whoever can see that thread.
- The per-game counts are stored across all servers but are not shown anywhere.

## Retention

History and stats stay until the operator deletes them. There is no public self-serve delete. Contact the operator (`/strife about`) to request deletion of your user row and match-player rows.

The operator can also wipe history with `strife/uninstall <key> confirm` (that game’s matches and stats) or `strife/dbreset confirm` (the whole database).

## Self-hosted

If you run your own copy, you are the operator. This page does not apply to that database. Point Discord’s privacy-policy URL at a page you control.

## Third parties

The bot talks to Discord and to the PostgreSQL database you configure. It does not send player data to analytics or ads.
