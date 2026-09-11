# Platform emoji

These emojis are for the bot (lobbies/menus). 

They can be used by games with `base=True`:

```python
ctx.emoji.get("loading", base=True)
```

For security reasons, the names are restricted to: the ones in [`strife/presentation/base_emojis.py`](../../strife/presentation/base_emojis.py). 

`strife/emoji` uploads a file from this folder only if its stem is in that file.

If you are looking for game-specific emoji, check the game's folder:

```
strife/games/coup/emoji/duke.webp   # coup_duke
plugins/my_game/emoji/token.webp    # my_game_token
```

Then `ctx.emoji.get("duke")` from within coup resolves the `coup_duke` emoji without issues :)


