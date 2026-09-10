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

## Retention

History and stats stay until the operator deletes the database. There is no public self-serve delete. Contact the operator (`/strife about`) to request deletion of your user row and match-player rows.

## Self-hosted

If you run your own copy, you are the operator. This page does not apply to that database. Point Discord’s privacy-policy URL at a page you control.

## Third parties

The bot talks to Discord and to the PostgreSQL database you configure. It does not send player data to analytics or ads.
