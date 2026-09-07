from __future__ import annotations

from strife.engine import (
    BotSpec,
    GameContext,
    GameOutcome,
    Move,
    PlayerCount,
    PlayerOrder,
    TurnBasedGame,
    game_metadata_from,
)
from strife.presentation.components import ActionRow, Button, ButtonStyle, LayoutView
from strife.presentation.game_ui import action_status, add_controls, game_container


@game_metadata_from(
    key="my_game",
    name="My Game",
    summary="TODO: one-line summary",
    description="TODO: longer description for the catalog.",
    tags=("dev",),
    author="TODO",
    version="1.0.0",
    platform_version="1.0.0",
    time_estimate="5m",
    difficulty=1,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(BotSpec("easy", "Random legal move"),),
)
class MyGame(TurnBasedGame):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.current = 0
        self.reset()

    def reset(self) -> None:
        self.current = 0

    def apply_move(self, move: Move) -> None:
        if move.source == "pass" and move.actor_seat is not None:
            self.current = 1 - move.actor_seat

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str:
        return action_status(ctx, self.players[next_actor])

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            move = await self.take_turn(
                ctx,
                {"pass"},
                lead=action_status(ctx, self.players[seat]),
                prefix_emoji="loading",
            )
            if move.source == "pass":
                opponent = 1 - seat
                return GameOutcome(
                    results={seat: "win", opponent: "loss"},
                    summary={"winner": seat},
                    description=f"{self.players[seat]} wins",
                    player_descriptions={seat: "Won", opponent: "Lost"},
                )

    def render(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
    ) -> LayoutView:
        view = LayoutView()
        container = game_container(ctx, lead=lead, prefix_emoji=prefix_emoji)
        row = ActionRow()
        row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.PRIMARY))
        add_controls(container, ctx, row)
        view.add_container(container)
        return view

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        return Move(actor_seat=seat, source="pass", args={})
