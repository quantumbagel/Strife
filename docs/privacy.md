# Privacy

Strife is a Discord bot. If you invite the public instance, this is what it stores.

## Data stored

- Discord guild (server) IDs and an optional default lobby channel
- Discord user IDs and display names of people who play
- Finished match records: game, settings, seed, outcome, and the move log used for replays
- Per-user win / loss / draw / played counts by game

Live lobbies and in-progress matches live in memory. A bot restart abandons them; they are not written until the match ends.

## What is not stored

- Message content from general chat
- Email addresses, IPs, or billing data
- Hidden information beyond what the match log needs for replay (for example a recorded night outcome, not every peek click)

## Why

Matches are saved so players can open `/strife replay` and `/strife profile`. Guild IDs exist so `/strife server` can remember the default lobby channel.

## Retention

Match history and stats stay until the operator deletes the database. There is no public self-serve deletion command. Contact the bot operator (see `/strife about`) to request deletion of your user row and match-player rows.

## Self-hosted instances

If you run your own copy, you are the operator. This page does not apply to that database. Point Discord's privacy-policy URL at a page you control.

## Third parties

The bot talks to Discord and to the PostgreSQL database you configure. It does not send player data to other analytics or advertising services.
