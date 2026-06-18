# 09 - Commands: Slash Tree & Owner Admin

User-facing slash commands and privileged owner message-commands (Architecture section 5). `/strife feedback` and a standalone `/strife history` are removed; history is folded into `/strife profile` ([10](10-replay-and-profile.md)). Depends on [02](02-configuration-and-emoji.md), [03](03-persistence.md), [07](07-matchmaking-and-lobby.md), [08](08-session-lifecycle.md), [10](10-replay-and-profile.md).

---

## 1. Slash command tree

Built with `discord.app_commands`. `/play` is top-level; everything else lives under a `/strife` group, with a nested `bot` subgroup (Discord allows group -> subgroup -> command).

- `/play [game_key] [private]` - create a lobby ([07](07-matchmaking-and-lobby.md)). `game_key` autocompletes from enabled games; `private` defaults false.
- `/strife catalog [page]` - paginated game browser with media-gallery previews.
- `/strife profile [user] [game] [page]` - merged stats + recent matches ([10](10-replay-and-profile.md)).
- `/strife settings [private]` - open the caller's active-lobby settings panel ([07](07-matchmaking-and-lobby.md) section 6B); `private` is an optional quick privacy toggle.
- `/strife forfeit` - surrender in the caller's current game thread ([08](08-session-lifecycle.md)).
- `/strife replay [match_ref]` - open the replay viewer ([10](10-replay-and-profile.md)).
- `/strife set_channel [channel]` - (Administrator only) set the guild's default thread/announcement channel.
- `/strife bot add [difficulty] [number]` - queue bots in the caller's lobby (creator only).
- `/strife bot remove [name]` - remove a queued bot (creator only).

### Modules

- `strife/commands/play.py` - the `/play` command + `game_key` autocomplete (lists `registry.all()` filtered by `games.yaml` `enabled`).
- `strife/commands/strife_group.py` - the `/strife` group, `bot` subgroup, and all subcommands.
- `strife/commands/admin.py` - the owner `on_message` listener (Section 4).

### Delegation

Commands are thin; they validate args and call services:

- `/play` -> `LobbyService.create_lobby` ([07](07-matchmaking-and-lobby.md) section 8).
- `/strife settings` / `/strife bot ...` -> `LobbyService` handlers (creator-gated against the caller's active lobby in `registries.user_location`).
- `/strife forfeit` -> `LifecycleService.forfeit` ([08](08-session-lifecycle.md)).
- `/strife replay` / `/strife profile` -> `ReplayService` / `ProfileService` ([10](10-replay-and-profile.md)).
- `/strife catalog` -> `CatalogView` (Section 3).
- `/strife set_channel` -> `GuildRepository.set_default_channel` ([03](03-persistence.md)), guarded by `app_commands.checks.has_permissions(administrator=True)`.

Autocomplete callbacks: `game_key` (enabled games), `game` on profile (games the user has played), and `match_ref` on replay (the user's / guild's recent match codes).

---

## 2. Command registration

- Commands are added to `bot.tree` in `setup_hook` step 9 ([01](01-foundation.md)) but **not** auto-synced (Discord rate limits). Syncing is manual via `strife/sync` (Section 4).
- Slash-move commands declared by games (`GameMetadata.slash_moves`, [06](06-game-engine-api.md)) are surfaced as in-thread game moves routed through `g_move`/`g_select` ([05](05-interaction-routing.md)); they are not separate global slash commands. (Games expose moves primarily as buttons/selects on the board; the slash-move metadata documents the move surface and powers parameter autocomplete where a game opts into a slash-style move.)

---

## 3. `/strife catalog` (Architecture "Interactive Media Catalogs")

- Ephemeral, paginated view built from `registry.all()` metadata: name, summary, author, time estimate, difficulty, tags, plus a `MediaGallery` of preview images (image paths declared per game in `games.yaml` or `assets/`).
- Pagination uses stateless buttons with a `cat_nav:` prefix (resource_id = caller user id; payload `{page}`), registered in the routing prefix table ([05](05-interaction-routing.md)) and delegated by the router to the catalog navigator. This keeps catalog navigation working without relying on a discord.py view timeout.

---

## 4. Owner admin message-commands

A `on_message` listener intercepts messages whose content starts with `strife/`, authored by a user in `settings.owner_ids` ([01](01-foundation.md)). It parses the namespace command and arguments and dispatches.

```python
class AdminCommands(commands.Cog):
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.id not in self.settings.owner_ids: return
        if not message.content.startswith("strife/"): return
        cmd, *args = shlex.split(message.content.removeprefix("strife/"))
        await self._dispatch(cmd, args, message)
```

### `strife/sync [guild_id | "local"]`

1. No args -> `await bot.tree.sync()` (global push of the command tree).
2. `"local"` -> `bot.tree.copy_global_to(guild=message.guild)`, then `sync(guild=message.guild)`.
3. A snowflake id -> same as local but for that specific guild object.
Reply with a summary of synced command count.

### `strife/clear [guild_id | "local"]`

1. No args -> `bot.tree.clear_commands(guild=None)`, then `sync()` (empties global).
2. `"local"` or snowflake -> clear + sync for that guild.

### `strife/treediff`

1. Fetch remotely registered commands (`await bot.tree.fetch_commands(guild=...)`).
2. Compare node-by-node against the local tree definitions (name, description, options/params, subcommands).
3. Reply with a text report of additions (local-only), removals (remote-only), and modifications (signature/option differences).

### `strife/dbreset`

1. Send a prominent confirmation (a danger button `Confirm Reset` + `Cancel`, owner-gated; or require typing `strife/dbreset confirm`).
2. On confirm: run `migrations/reset.sql` (drops all tables incl. `schema_migrations`) then re-run the `Migrator` ([03](03-persistence.md)), recreating empty tables.
3. Reply with completion status.

### `strife/emoji`

Run the destructive emoji re-upload routine ([02](02-configuration-and-emoji.md) section 5): delete existing application emojis, upload each image in `assets/emoji/`, write the new name->id map into `config/emoji.yaml`, reload the resolver cache. Reply with the count uploaded.

---

## 5. Permissions & safety

- All `strife/*` admin commands are restricted to `owner_ids`; non-owners are ignored silently.
- `strife/dbreset` requires explicit confirmation.
- `strife/clear` and `strife/sync` reply with what changed; never sync on a loop.
- `/strife set_channel` requires Administrator; failures reply ephemerally with `common.forbidden`.

---

## 6. Deliverables checklist

- [ ] `strife/commands/play.py` (`/play` + autocomplete).
- [ ] `strife/commands/strife_group.py` (`/strife` group, `bot` subgroup, all subcommands, autocompletes, set_channel permission check).
- [ ] `strife/commands/admin.py` (`AdminCommands` cog: sync/clear/treediff/dbreset/emoji).
- [ ] Catalog navigator + `cat_nav:` prefix wired into the router.
- [ ] Commands registered (not auto-synced) in `setup_hook`; admin cog added.
