from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
from strife.games.liars_dice.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
    TextDisplay,
)
from strife.presentation.game_frame import add_game_header
from strife.presentation.roster import player_mention


class LiarsDice(Game):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        # Starting dice count for each player
        start_dice = settings.get("dice_count", 5)
        self.dice_counts: dict[int, int] = {p.seat: start_dice for p in players}
        self.hands: dict[int, list[int]] = {}
        self.current_bid: tuple[int, int] | None = None
        self.last_bidder: int | None = None
        self.current = self.rng.randint(0, len(players) - 1)
        self.alive: set[int] = {p.seat for p in players}
        self.history: list[str] = []

    def _next_player(self, current: int) -> int:
        n = len(self.players)
        seat = (current + 1) % n
        while seat not in self.alive:
            seat = (seat + 1) % n
        return seat

    def _die_emoji(self, ctx: GameContext, val: int) -> str:
        fallback = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}.get(val, "?")
        return ctx.emoji.get(f"die_{val}") or fallback

    def _format_hand(self, ctx: GameContext, hand: list[int]) -> str:
        return " ".join(self._die_emoji(ctx, v) for v in sorted(hand))

    def _turn_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        turn_label = player_mention(
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
        )
        return f"{turn_label}'s turn to bid or challenge"

    async def play(self, ctx: GameContext) -> GameOutcome:
        while len(self.alive) > 1:
            # 1. Roll dice for all alive players
            for seat in self.alive:
                self.hands[seat] = sorted(self.rng.randint(1, 6) for _ in range(self.dice_counts[seat]))

            await ctx.record_action("round_start", {
                "hands": {seat: list(hand) for seat, hand in self.hands.items()},
                "dice_counts": dict(self.dice_counts),
            })

            # Send private hands
            for seat in self.alive:
                priv_view = LayoutView()
                priv_container = Container()
                add_game_header(
                    priv_container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Your Secret Dice Hand",
                    status=f"Your rolled hand: {self._format_hand(ctx, self.hands[seat])}",
                    is_replay=ctx.is_replay,
                )
                priv_view.add_container(priv_container)
                await ctx.send_private(seat, priv_view)

            self.current_bid = None
            self.last_bidder = None

            # 2. Bidding loop
            while True:
                seat = self.current
                view = self._round_view(
                    ctx,
                    title=f"Liar's Dice - {sum(self.dice_counts.values())} Total Dice",
                    status=self._turn_status(ctx, seat),
                    status_emoji="loading",
                )

                sources = {"bid", "challenge"}
                move = await ctx.request_input(view, actor=seat, sources=sources)

                if move.source == "challenge":
                    # Challenge the last bid!
                    if self.last_bidder is None or self.current_bid is None:
                        continue  # Safety check

                    challenger = seat
                    bidder = self.last_bidder
                    bid_q, bid_v = self.current_bid

                    # Count total matching dice on the table
                    wilds = self.settings.get("wild_ones", True)
                    actual_count = sum(
                        hand.count(bid_v) + (hand.count(1) if wilds and bid_v != 1 else 0)
                        for hand in self.hands.values()
                    )

                    # Determine loser
                    is_liar = actual_count < bid_q
                    loser = bidder if is_liar else challenger
                    winner = challenger if is_liar else bidder

                    self.dice_counts[loser] -= 1
                    loser_name = self.players[loser].display_name
                    winner_name = self.players[winner].display_name

                    # Build outcome message
                    reveal_text = f"**The Bid was {bid_q} Fives (⚄) or similar.**\n"
                    reveal_text = f"**Bid**: {bid_q} × {self._die_emoji(ctx, bid_v)}\n"
                    reveal_text += f"**Actual Count**: {actual_count} × {self._die_emoji(ctx, bid_v)}\n\n"
                    
                    reveal_text += "**All Dice Revealed:**\n"
                    for s in sorted(self.alive):
                        reveal_text += f"• {self.players[s].display_name}: {self._format_hand(ctx, self.hands[s])}\n"

                    verdict = f"{winner_name} was correct! {loser_name} loses 1 die."
                    if self.dice_counts[loser] == 0:
                        self.alive.remove(loser)
                        verdict += f" {loser_name} is eliminated from the game!"
                        self.history.append(f"{loser_name} was eliminated.")
                    else:
                        self.history.append(f"{loser_name} lost 1 die (remaining: {self.dice_counts[loser]}).")

                    self.history.append(f"Round challenge: {winner_name} challenged {loser_name}'s bid of {bid_q}x{bid_v}. Actual: {actual_count}.")

                    await ctx.record_action("challenge_resolve", {
                        "challenger": challenger,
                        "bidder": bidder,
                        "bid": [bid_q, bid_v],
                        "actual_count": actual_count,
                        "loser": loser,
                        "verdict": verdict,
                        "history": list(self.history),
                    })

                    # Show challenge outcome screen
                    outcome_view = LayoutView()
                    outcome_container = Container()
                    add_game_header(
                        outcome_container,
                        ctx.emoji,
                        game_key=self.metadata.key,
                        game_name=self.metadata.name,
                        title="Challenge Result!",
                        status=verdict,
                        status_emoji="success" if loser == bidder else "error",
                        is_replay=ctx.is_replay,
                    )
                    outcome_container.add_text(TextDisplay(reveal_text))
                    outcome_view.add_container(outcome_container)
                    await ctx.update(outcome_view)

                    # Small delay so players can see the result
                    await asyncio.sleep(4)

                    # Loser starts the next round if they are still alive, otherwise next alive
                    self.current = loser if loser in self.alive else self._next_player(loser)
                    break

                elif move.source == "bid":
                    # Parse and record new bid
                    quantity = int(move.args.get("quantity", 0))
                    value = int(move.args.get("value", 0))

                    # Validate bid is higher
                    is_valid = True
                    if self.current_bid is not None:
                        curr_q, curr_v = self.current_bid
                        if quantity < curr_q or (quantity == curr_q and value <= curr_v):
                            is_valid = False

                    if not is_valid:
                        # Resend turn without changing state
                        continue

                    self.current_bid = (quantity, value)
                    self.last_bidder = seat
                    self.current = self._next_player(seat)
                    await ctx.record_action("bid", {
                        "player": seat,
                        "quantity": quantity,
                        "value": value,
                    })

        # Game over, final player wins
        final_winner = next(iter(self.alive))
        winner_mention = str(self.players[final_winner])
        results = {p.seat: "win" if p.seat == final_winner else "loss" for p in self.players}
        player_descriptions = {
            p.seat: "Won the game!" if p.seat == final_winner else "Eliminated"
            for p in self.players
        }

        return GameOutcome(
            results=results,
            summary={"winner": final_winner, "history": list(self.history)},
            description=f"{winner_mention} won!",
            player_descriptions=player_descriptions,
        )

    def _round_view(
        self,
        ctx: GameContext,
        *,
        title: str,
        status: str | None = None,
        status_emoji: str | None = None,
    ) -> LayoutView:
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title=title,
            status=status,
            status_emoji=status_emoji,
            is_replay=ctx.is_replay,
        )

        # Build table status text
        table_text = "**Roster and Dice Counts:**\n"
        for p in self.players:
            alive_status = "🎲" * self.dice_counts[p.seat] if p.seat in self.alive else "☠️ (Out)"
            table_text += f"• {p.display_name}: {alive_status}\n"

        if self.current_bid is not None:
            bid_q, bid_v = self.current_bid
            bidder_name = self.players[self.last_bidder].display_name if self.last_bidder is not None else "?"
            table_text += f"\n👉 **Current Bid**: {bid_q} × {self._die_emoji(ctx, bid_v)} (by {bidder_name})"
        else:
            table_text += "\n👉 **Current Bid**: None (Opening bid)"

        container.add_text(TextDisplay(table_text))

        # Turn inputs
        total_dice = sum(self.dice_counts.values())
        min_q = 1
        if self.current_bid is not None:
            min_q = self.current_bid[0]

        # Quantity Choices (from min_q to total_dice)
        quantity_choices = [
            SelectChoice(label=str(q), value=str(q))
            for q in range(min_q, total_dice + 1)
        ][:25] # Discord limits select choices to 25

        # Value Choices (2 to 6, and 1 if wilds are disabled)
        val_start = 1 if not self.settings.get("wild_ones", True) else 2
        value_choices = [
            SelectChoice(label=f"Value {v}", value=str(v), emoji=f"die_{v}")
            for v in range(val_start, 7)
        ]

        # Controls ActionRow
        row1 = ActionRow()
        row1.add_select(
            Select(
                source="quantity_select",
                placeholder="Choose quantity",
                choices=quantity_choices,
            )
        )
        container.add_action_row(row1)

        row2 = ActionRow()
        row2.add_select(
            Select(
                source="value_select",
                placeholder="Choose die value",
                choices=value_choices,
            )
        )
        container.add_action_row(row2)

        row3 = ActionRow()
        row3.add_button(
            Button(
                source="bid",
                label="Submit Bid",
                style=ButtonStyle.PRIMARY,
                disabled=ctx.is_replay,
            )
        )
        row3.add_button(
            Button(
                source="challenge",
                label="Call Liar!",
                style=ButtonStyle.DANGER,
                disabled=self.last_bidder is None or ctx.is_replay,
            )
        )
        row3.add_button(
            Button(
                source="peek",
                label="Peek Hand",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                disabled=ctx.is_replay,
            )
        )
        container.add_action_row(row3)

        view.add_container(container)
        return view

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner_seat = summary.get("winner")
        view = LayoutView()
        container = Container()
        winner_name = self.players[winner_seat].display_name if winner_seat is not None else "Unknown"
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title="Game Over!",
            status=f"{winner_name} wins the match!",
            status_emoji="success",
            is_replay=ctx.is_replay,
        )

        history_text = "**Match History:**\n" + "\n".join(f"• {item}" for item in self.history[-10:])
        container.add_text(TextDisplay(history_text))
        view.add_container(container)
        return view

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.dice_counts = {p.seat: self.settings.get("dice_count", 5) for p in self.players}
        self.hands = {}
        self.alive = {p.seat for p in self.players}
        self.current_bid = None
        self.last_bidder = None
        self.history = []

        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        for i, move in enumerate(moves):
            takeover_info = None
            if move.arguments.get("replaced_by_bot"):
                for p in self.players:
                    if p.seat == move.actor_seat:
                        p.is_bot = True
                        p.bot_difficulty = "hard"
                        takeover_info = {
                            "user_id": p.user_id,
                            "display_name": p.display_name,
                            "is_bot": p.is_bot,
                            "type": "bot_takeover",
                            "reason": move.arguments.get("replace_reason", "timeout"),
                        }

            if move.source == "round_start":
                self.hands = {int(k): v for k, v in move.arguments["hands"].items()}
                self.dice_counts = {int(k): v for k, v in move.arguments["dice_counts"].items()}
                self.current_bid = None
                self.last_bidder = None
                view = self._round_view(ctx, title="Round Start", status="Dice rolled!")
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Round Start (Turn {len(frames)+1})",
                    actor_seat=None,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "bid":
                self.current_bid = (move.arguments["quantity"], move.arguments["value"])
                self.last_bidder = move.actor_seat
                view = self._round_view(ctx, title="Bid Placed", status=f"Bid submitted by {self.players[move.actor_seat].display_name}")
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Bid (Turn {len(frames)+1})",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "challenge_resolve":
                self.history = move.arguments.get("history", [])
                loser = move.arguments["loser"]
                self.dice_counts[loser] -= 1
                if self.dice_counts[loser] == 0:
                    self.alive.discard(loser)
                
                # Show challenge resolution view
                verdict = move.arguments["verdict"]
                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Challenge Result!",
                    status=verdict,
                    is_replay=ctx.is_replay,
                )
                view.add_container(container)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Challenge (Turn {len(frames)+1})",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

        return frames

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        return await asyncio.to_thread(choose_move, self, difficulty, seat)

    def peek_info(self, seat: int, ctx: GameContext) -> str:
        hand = self.hands.get(seat, [])
        if not hand:
            return "You have no dice left in this round."
        return f"🎲 **Your Secret Dice Hand:** {self._format_hand(ctx, hand)}"
