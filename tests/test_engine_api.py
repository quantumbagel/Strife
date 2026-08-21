from __future__ import annotations

from strife.engine import Move, iter_replay, select_value
from strife.engine.log import LogEntryKind
from strife.engine.players import Player
from strife.engine.replay import ReplayBuilder, freeze_view
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
    move_sources,
    query_sources,
)


def test_move_args_alias() -> None:
    move = Move(actor_seat=0, source="tile_00", args={"value": "x"})
    assert move.args is move.arguments
    assert move.is_game
    assert not move.is_system


def test_select_value_shapes() -> None:
    assert select_value(Move(0, "s", {"value": "a"})) == "a"
    assert select_value(Move(0, "s", {"values": ["b", "c"]})) == "b"
    assert select_value(Move(0, "s", {"target": "9", "value": "1"}), "target") == "9"
    assert select_value(Move(0, "s", {})) is None


def test_query_and_move_sources() -> None:
    view = LayoutView()
    container = Container()
    row = ActionRow()
    row.add_button(Button(source="pass", label="Pass"))
    row.add_button(Button(source="peek", label="Peek", query=True))
    row.add_button(Button(label="Rules", style=ButtonStyle.LINK, url="https://example.com"))
    row.add_select(Select(source="vote", choices=[SelectChoice(label="A", value="a")]))
    container.add_action_row(row)
    view.add_container(container)
    assert move_sources(view) == {"pass", "vote"}
    assert query_sources(view) == {"peek"}


def test_freeze_view_does_not_mutate_original() -> None:
    view = LayoutView()
    container = Container()
    row = ActionRow()
    button = Button(source="go", label="Go")
    row.add_button(button)
    container.add_action_row(row)
    view.add_container(container)
    frozen = freeze_view(view)
    assert button.disabled is False
    frozen_button = frozen.containers[0].children[0].items[0]
    assert frozen_button.disabled is True


def test_iter_replay_coalesces_takeover() -> None:
    players = [
        Player(seat=0, user_id=1, display_name="A"),
        Player(seat=1, user_id=2, display_name="B"),
    ]
    moves = [
        Move(0, "tile_00", {}),
        Move(
            None,
            "bot_takeover",
            {"seat": 1, "reason": "timeout", "bot_difficulty": "hard"},
            kind=LogEntryKind.SYSTEM,
        ),
        Move(1, "tile_11", {}),
        Move(None, "game_end", {"reason": "done"}, kind=LogEntryKind.SYSTEM),
    ]
    steps = list(iter_replay(moves, players))
    assert [s.frame for s in steps] == [True, False, True, True]
    takeover_step = steps[2]
    assert takeover_step.takeover is not None
    assert takeover_step.takeover["type"] == "bot_takeover"
    assert steps[-1].terminal is True
    assert players[1].is_bot is True


class _ReplayCtx:
    started_at = None
    is_replay = True


def test_replay_builder_skips_non_frames() -> None:
    players = [Player(seat=0, user_id=1, display_name="A")]
    moves = [
        Move(None, "bot_takeover", {"seat": 0}, kind=LogEntryKind.SYSTEM),
        Move(0, "pass", {}),
    ]
    builder = ReplayBuilder(_ReplayCtx())  # type: ignore[arg-type]
    view = LayoutView()
    view.add_container(Container())
    builder.initial(view, label="Start")
    for step in iter_replay(moves, players):
        builder.add(step, view, label="Turn")
    frames = builder.build()
    assert [f.turn_label for f in frames] == ["Start", "Turn"]
    assert frames[1].takeover_info is not None
