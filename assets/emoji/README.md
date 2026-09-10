# Platform emoji

Host chrome only. Plugin art does not belong here.

Games resolve these names with `base=True`:

```python
ctx.emoji.get("loading", base=True)
```

Names: [`strife/presentation/base_emojis.py`](../../strife/presentation/base_emojis.py). `strife/emoji` uploads a file from this folder only if its stem is on that list.

Game art lives in the plugin:

```
strife/games/coup/emoji/duke.webp   # coup_duke
plugins/my_game/emoji/token.webp    # my_game_token
```

Then `ctx.emoji.get("duke")` (game bound) is `coup_duke`.
