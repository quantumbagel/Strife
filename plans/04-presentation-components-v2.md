# 04 - Presentation: Components V2

Strife's own component abstraction (Architecture section 2), the compiler that turns it into `discord.ui.*` objects, and the message manager that renders and edits messages. Per Decision D5 the engine never hands discord.py objects to game code; games build Strife components, and updates happen by re-rendering the tree and editing the message. Depends on [01-foundation.md](01-foundation.md) and [02-configuration-and-emoji.md](02-configuration-and-emoji.md).

---

## 1. Why a custom abstraction

- Decouples game code from discord.py's API surface (games stay stable across library changes).
- Lets the engine own `custom_id` assignment ([05](05-interaction-routing.md)) so games describe *intent* (a move source + payload), not transport.
- Centralizes validation of Discord's Components V2 limits before an API call is attempted.

The class model mirrors the architecture's class diagram exactly (`LayoutView`, `Container`, `TextDisplay`, `Separator`, `MediaGallery`, `MediaGalleryItem`, `ActionRow`, `Button`, `Select`).

---

## 2. Component classes - `strife/presentation/components.py`

Plain dataclasses (pure data; no Discord imports).

```python
class TextSize(StrEnum):
    HEADER = "header"        # compiled to "## " prefix
    SUBHEADER = "subheader"  # compiled to "### "
    BODY = "body"            # no prefix

class Align(StrEnum):
    LEFT = "left"; CENTER = "center"; RIGHT = "right"   # advisory (see Section 4)

class ButtonStyle(StrEnum):
    PRIMARY = "primary"; SECONDARY = "secondary"
    SUCCESS = "success"; DANGER = "danger"; LINK = "link"

@dataclass
class TextDisplay:
    markdown_content: str
    size_style: TextSize = TextSize.BODY
    alignment: Align = Align.LEFT

@dataclass
class Separator:
    visible: bool = True

@dataclass
class MediaGalleryItem:
    media_url: str
    description: str | None = None

@dataclass
class MediaGallery:
    items: list[MediaGalleryItem] = field(default_factory=list)
    def add_item(self, item: MediaGalleryItem) -> "MediaGallery": ...

@dataclass
class Button:
    source: str                      # logical id (engine maps to custom_id); unused for LINK
    label: str
    style: ButtonStyle = ButtonStyle.SECONDARY
    emoji: str | None = None         # semantic emoji name, resolved at compile time
    url: str | None = None           # LINK style only
    disabled: bool = False
    payload: dict | None = None      # routing payload (engine -> custom_id)

@dataclass
class SelectChoice:
    label: str; value: str; description: str | None = None; emoji: str | None = None; default: bool = False

@dataclass
class Select:
    source: str
    choices: list[SelectChoice] = field(default_factory=list)
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    payload: dict | None = None

@dataclass
class ActionRow:
    items: list[Button | Select] = field(default_factory=list)
    def add_button(self, b: Button) -> "ActionRow": ...
    def add_select(self, s: Select) -> "ActionRow": ...

@dataclass
class Container:
    accent_color: int | None = None
    children: list[TextDisplay | Separator | MediaGallery | ActionRow] = field(default_factory=list)
    def add_text(self, t: TextDisplay) -> "Container": ...
    def add_separator(self, s: Separator | None = None) -> "Container": ...
    def set_gallery(self, g: MediaGallery) -> "Container": ...
    def add_action_row(self, r: ActionRow) -> "Container": ...

@dataclass
class LayoutView:
    children: list[Container | ActionRow | TextDisplay | Separator | MediaGallery] = field(default_factory=list)
    def add_container(self, c: Container) -> "LayoutView": ...
    def add_action_row(self, r: ActionRow) -> "LayoutView": ...
    def header(self, emoji_name: str, title: str) -> "LayoutView": ...  # adds "## [emoji] title"
```

Note the architecture's `Button.id` / `Select.id` are represented as `source` (the engine combines `source` + `payload` into a `custom_id`). LINK buttons carry a `url` and need no `source`.

### Builder helpers

`strife/presentation/builders.py` provides small factories for repeated patterns: `header_text`, `divider`, `mention_list`, `paginator_row(prev_src, next_src, page, total)`. Keeps game and view code terse.

---

## 3. Compiler - `strife/presentation/compiler.py`

Converts a Strife `LayoutView` into a `discord.ui.LayoutView` instance ready to attach to a message. Stateless function plus a thin `Compiler` holding the `EmojiResolver` and a `custom_id` encoder ([05](05-interaction-routing.md)).

```python
class Compiler:
    def __init__(self, emoji: EmojiResolver, encoder: CustomIdEncoder): ...
    def compile(self, view: LayoutView, *, resource_id: int, prefix: str) -> discord.ui.LayoutView: ...
```

Mapping (Strife -> discord.py 2.6):

- `LayoutView` -> `discord.ui.LayoutView` (timeout=None; we route globally, see Section 5).
- `Container` -> `discord.ui.Container(accent_colour=...)`.
- `TextDisplay` -> `discord.ui.TextDisplay(content=...)` with the size prefix prepended (`## `, `### `, or none).
- `Separator` -> `discord.ui.Separator(visible=...)`.
- `MediaGallery` -> `discord.ui.MediaGallery` of `discord.MediaGalleryItem(media=...)`.
- `ActionRow` -> `discord.ui.ActionRow` populated with compiled buttons/selects.
- `Button` -> `discord.ui.Button(style=..., label=..., emoji=resolved, disabled=..., custom_id=encoded)`; LINK style sets `url` and omits `custom_id`.
- `Select` -> `discord.ui.Select(options=[discord.SelectOption(...)], min_values, max_values, disabled, custom_id=encoded)`.

`custom_id` assignment: for each interactive component the compiler calls `encoder.encode(prefix, resource_id, source, payload)` ([05](05-interaction-routing.md)). Emoji strings (`button.emoji`, `choice.emoji`) are resolved via `EmojiResolver` to either a unicode char or `<:name:id>`.

### Validation (raise `LayoutError` before any API call)

- Total compiled components <= 40 (LayoutView limit, including nested).
- ActionRow holds either up to 5 buttons OR exactly 1 select (never both).
- `TextDisplay` content <= 4000 chars.
- Each `custom_id` <= 100 chars (the encoder guarantees this via overflow offload, [05](05-interaction-routing.md)); the compiler asserts it.
- LINK buttons have a `url` and no `source`; non-LINK buttons have a `source`.

---

## 4. Alignment & size handling

- `size_style` is realized through markdown heading prefixes (header `## `, subheader `### `). This matches the architecture's required header style `## [emoji] Name`.
- `alignment` has no native Components V2 equivalent; it is **advisory** in v1 (recorded but compiled as a no-op). Documented so games do not assume centered text renders centered.

---

## 5. Message manager - `strife/presentation/message.py`

Owns sending and updating a Strife view on a Discord message. The "stateful component updates / delta sync" of the architecture is implemented as: mutate the Strife tree in place, then re-compile and `edit`.

```python
class ViewSurface:
    """Binds a Strife LayoutView to one Discord message and keeps them in sync."""
    def __init__(self, compiler: Compiler, *, prefix: str, resource_id: int): ...

    async def send(self, target, view: LayoutView, *, ephemeral: bool = False) -> None
    async def send_to_thread(self, thread, view: LayoutView) -> None
    async def update(self, view: LayoutView) -> None       # re-compile + message.edit
    async def replace(self, view: LayoutView) -> None       # full new message (rematch reset)
    async def disable_all(self) -> None                     # set every interactive item disabled, update
```

- `send`/`update` set `content=None` and `embeds=[]` so the message uses Components V2 exclusively (required when editing a legacy message into V2).
- `update` is the hot path: game mutates buttons/selects (e.g. `disabled=True`, changed `choices`) and calls `update`; the surface re-compiles and edits the same message.
- `disable_all` is used when a game ends or a turn locks; it walks the Strife tree, sets `disabled=True` on every `Button`/`Select`, and updates.
- Ephemeral responses (settings panel, errors) go through `send(interaction, ..., ephemeral=True)`.

### Dispatch boundary

The compiled `discord.ui.LayoutView` is a render target only. Component clicks are delivered to the global interaction handler and forwarded to the `InteractionRouter` ([05](05-interaction-routing.md)); Strife does **not** rely on discord.py per-item view callbacks. The architecture's "callback handler" bound to a `Button`/`Select` is an engine-level concept ([06](06-game-engine-api.md)) the router invokes after parsing the `custom_id`.

---

## 6. Example (Tic-Tac-Toe turn view, abbreviated)

```python
view = (LayoutView()
    .header("brand_logo", "Tic-Tac-Toe")
    .add_container(Container(accent_color=0x57F287, children=[
        TextDisplay("It's **X**'s turn.", size_style=TextSize.SUBHEADER),
    ])))
for row in range(3):
    ar = ActionRow()
    for col in range(3):
        ar.add_button(Button(source=f"tile_{col}{row}", label="·",
                             style=ButtonStyle.SECONDARY))
    view.add_action_row(ar)
# engine sends with prefix "g_move", resource_id=<match_id>
await surface.update(view)
```

The Peek/Spectate action row from Architecture section 6D is intentionally omitted (out of scope, Decision: cut spectator/peek).

---

## 7. Deliverables checklist

- [ ] `strife/presentation/components.py` (dataclasses + enums + builder methods above).
- [ ] `strife/presentation/builders.py` (header/divider/mention-list/paginator helpers).
- [ ] `strife/presentation/compiler.py` (`Compiler.compile` + `LayoutError` validation).
- [ ] `strife/presentation/message.py` (`ViewSurface` send/update/replace/disable_all).
- [ ] Size-prefix + emoji resolution + Components V2 limit checks.
- [ ] Unit tests: tree -> compiled structure, limit violations raise, disable_all walks nested trees ([13](13-testing.md)).
