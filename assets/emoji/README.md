# Platform (base) emoji

Files in this folder are the **host** set. Plugin art does not belong here.

Games resolve these names only with `base=True`:

```python
ctx.emoji.get("loading", base=True)
```

The canonical name list is [`strife/presentation/base_emojis.py`](../../strife/presentation/base_emojis.py). `strife/emoji` uploads a file from this folder only when its stem is in that list.

Game-specific art goes in the plugin package:

```
strife/games/coup/emoji/duke.webp   # Discord name: coup_duke
plugins/my_game/emoji/token.webp    # Discord name: my_game_token
```

Then `ctx.emoji.get("duke")` (with the game bound) resolves `coup_duke`.
