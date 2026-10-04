# Privacy

Strife is a Discord bot. If you invite the public instance, this is what it stores.

## Stored

- Guild (server) IDs and an optional default lobby channel
- User IDs and display names of people who play
- Finished matches: game, settings, seed, outcome, move log (for replay)
- Per-user win / loss / draw / played counts by game

Live lobbies and in-progress matches stay in the memory of the program and are not themselve stored.

## Not stored

- Messages
- Any Discord account information

## Why

So `/strife replay` and `/strife profile` work. Guild IDs so `/strife server` can remember the default lobby channel.

## Who can see what

- Every match is tied to the server it was played in.
- `/strife profile` shows a player's wins, losses, draws, and recent matches **from the current server only**. Anyone in that server can look up any player, but games from other servers never show up.
- `/strife replay` only opens a match by its 6-character code, and only if it was played in the server where you run the command. Anyone in that server who has the code can watch the replay, including the display names, roles, and moves recorded in it.
- Results posted in a game thread are visible to whoever can see that thread.
- The per-game counts are stored across all servers but are not shown anywhere.

## Retention

History and stats stay until the operator deletes the database. There is no public self-serve delete. Contact the operator (`/strife about`) to request deletion of your user row and match-player rows.

## Self-hosted

If you run your own copy, you are the operator. This page does not apply to that database. Point Discord’s privacy-policy URL at a page you control.

## Third parties

The bot talks to Discord and to the PostgreSQL database you configure. It does not send player data to analytics or ads.
