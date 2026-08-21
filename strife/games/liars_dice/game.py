from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.workers import run_cpu
from strife.engine.players import GameOutcome, Move
from strife.engine.replay import ReplayBuilder, iter_replay
from strife.games.liars_dice.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
)
from strife.presentation.game_ui import message_lead, query_panel
from strife.presentation.roster import member_line
from strife.presentation.style import add_body, add_section, history_block


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
        self.pending_quantity: int | None = None
        self.pending_value: int | None = None

    def active_seats(self) -> set[int]:
        return set(self.alive)

    def _total_alive_dice(self) -> int:
        return sum(self.dice_counts[s] for s in self.alive)

    def _legal_face_values(self) -> list[int]:
        val_start = 1 if not self.settings.get("wild_ones", True) else 2
        return list(range(val_start, 7))

    def _is_max_bid(self) -> bool:
        if self.current_bid is None:
            return False
        total_dice = self._total_alive_dice()
        curr_q, curr_v = self.current_bid
        return curr_q == total_dice and curr_v == 6

    def _validate_bid(self, quantity: int, value: int) -> tuple[bool, str | None]:
        total_dice = self._total_alive_dice()
        legal_faces = self._legal_face_values()
        if quantity < 1 or quantity > total_dice:
            return False, f"Quantity must be between 1 and {total_dice}."
        if value not in legal_faces:
            return False, f"Die value must be one of: {', '.join(map(str, legal_faces))}."
        if self.current_bid is not None:
            curr_q, curr_v = self.current_bid
            if quantity < curr_q or (quantity == curr_q and value <= curr_v):
                return False, "Bid must be higher than the current bid."
        return True, None

    def _count_matching_dice(self, bid_v: int) -> int:
        wilds = self.settings.get("wild_ones", True)
        return sum(
            self.hands[seat].count(bid_v)
            + (self.hands[seat].count(1) if wilds and bid_v != 1 else 0)
            for seat in self.alive
            if seat in self.hands
        )

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

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        return f"{player.mention} to bid or challenge"

    async def play(self, ctx: GameContext) -> GameOutcome:
        while len(self.alive) > 1:
            # 1. Roll dice for all alive players
            for seat in self.alive:
                self.hands[seat] = sorted(self.rng.randint(1, 6) for _ in range(self.dice_counts[seat]))

            await ctx.record_event("round_start", {
                "hands": {seat: list(hand) for seat, hand in self.hands.items()},
                "dice_counts": dict(self.dice_counts),
            })

            # Send private hands
            for seat in self.alive:
                priv_view = LayoutView()
                priv_container = Container()
                message_lead(
                    priv_container,
                    f"Your rolled hand: {self._format_hand(ctx, self.hands[seat])}",
                    emoji=ctx.emoji,
                )
                priv_view.add_container(priv_container)
                await ctx.send_private(seat, priv_view)

            self.current_bid = None
            self.last_bidder = None
            self.pending_quantity = None
            self.pending_value = None

            # 2. Bidding loop
            while True:
                seat = self.current
                view = self._round_view(
                    ctx,
                    lead=self._action_status(ctx, seat),
                    prefix_emoji="loading",
                )

                sources = {"quantity_select", "value_select", "bid", "challenge"}
                move = await ctx.request_input(view, actor=seat, sources=sources, record=False)

                if move.source == "quantity_select":
                    val = move.args.get("value")
                    if val is not None:
                        self.pending_quantity = int(val)
                    continue

                if move.source == "value_select":
                    val = move.args.get("value")
                    if val is not None:
                        self.pending_value = int(val)
                    continue

                if move.source == "challenge":
                    # Challenge the last bid!
                    if self.last_bidder is None or self.current_bid is None:
                        continue  # Safety check

                    challenger = seat
                    bidder = self.last_bidder
                    bid_q, bid_v = self.current_bid

                    actual_count = self._count_matching_dice(bid_v)

                    # Determine loser
                    is_liar = actual_count < bid_q
                    loser = bidder if is_liar else challenger
                    winner = challenger if is_liar else bidder

                    self.dice_counts[loser] -= 1
                    loser_name = self.players[loser].mention
                    winner_name = self.players[winner].mention
                    challenger_name = self.players[challenger].mention
                    bidder_name = self.players[bidder].mention

                    bullet = ctx.emoji.get("bullet")
                    reveal_lines = [
                        f"**Bid:** {bid_q} × {self._die_emoji(ctx, bid_v)}",
                        f"**Actual count:** {actual_count} × {self._die_emoji(ctx, bid_v)}",
                        "",
                        "**All dice revealed**",
                    ]
                    for s in sorted(self.alive):
                        reveal_lines.append(
                            f"{bullet} {self.players[s].mention}: {self._format_hand(ctx, self.hands[s])}"
                        )
                    reveal_text = "\n".join(reveal_lines)

                    verdict = f"{winner_name} was correct. {loser_name} loses 1 die."
                    if self.dice_counts[loser] == 0:
                        self.alive.remove(loser)
                        self.hands.pop(loser, None)
                        verdict += f" {loser_name} is eliminated."
                        self.history.append(f"{loser_name} was eliminated.")
                    else:
                        self.history.append(f"{loser_name} lost 1 die (remaining: {self.dice_counts[loser]}).")

                    self.history.append(
                        f"{challenger_name} called liar on {bidder_name}'s bid of "
                        f"{bid_q}x{bid_v}. Actual: {actual_count}."
                    )

                    await ctx.record_event("challenge_resolve", {
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
                    message_lead(
                        outcome_container,
                        verdict,
                        emoji=ctx.emoji,
                        prefix_emoji="success" if loser == bidder else "error",
                    )
                    add_body(outcome_container, reveal_text)
                    outcome_view.add_container(outcome_container)
                    await ctx.update(outcome_view)

                    # Small delay so players can see the result
                    await asyncio.sleep(4)

                    # Loser starts the next round if they are still alive, otherwise next alive
                    self.current = loser if loser in self.alive else self._next_player(loser)
                    break

                elif move.source == "bid":
                    if self._is_max_bid():
                        continue

                    quantity = self.pending_quantity
                    value = self.pending_value
                    if quantity is None:
                        quantity = move.args.get("quantity")
                    if value is None:
                        value = move.args.get("value")
                    if quantity is None or value is None:
                        continue
                    quantity = int(quantity)
                    value = int(value)

                    is_valid, _reason = self._validate_bid(quantity, value)
                    if not is_valid:
                        continue

                    self.current_bid = (quantity, value)
                    self.last_bidder = seat
                    self.current = self._next_player(seat)
                    self.pending_quantity = None
                    self.pending_value = None
                    await ctx.record_event("bid", {
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

    def _round_view_replay(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
    ) -> LayoutView:
        view = LayoutView()
        container = Container()
        message_lead(container, lead, emoji=ctx.emoji, prefix_emoji=prefix_emoji)

        die_mark = ctx.emoji.get("game_liars_dice")
        roster_lines = []
        for p in self.players:
            name = member_line(
                ctx.emoji,
                user_id=p.user_id,
                display_name=p.display_name,
                is_bot=p.is_bot,
                bot_difficulty=p.bot_difficulty,
            )
            if p.seat in self.alive:
                dice = die_mark * self.dice_counts[p.seat]
                roster_lines.append(f"{name}: {dice}")
            else:
                roster_lines.append(f"{name}: {ctx.emoji.get('error')} Out")
        add_section(container, "Players", "\n".join(roster_lines))

        pointing = ctx.emoji.get("pointing")
        if self.current_bid is not None:
            bid_q, bid_v = self.current_bid
            bidder_name = self.players[self.last_bidder].mention if self.last_bidder is not None else "?"
            add_body(
                container,
                f"{pointing} **Current bid:** {bid_q} × {self._die_emoji(ctx, bid_v)} (by {bidder_name})",
            )
        else:
            add_body(container, f"{pointing} **Current bid:** none — opening bid")

        view.add_container(container)
        return view

    def _round_view(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
    ) -> LayoutView:
        view = self._round_view_replay(ctx, lead=lead, prefix_emoji=prefix_emoji)
        container = view.containers[0]

        total_dice = self._total_alive_dice()
        min_q = 1
        if self.current_bid is not None:
            min_q = self.current_bid[0]

        max_q = total_dice
        low_q = min_q
        if max_q - low_q + 1 > 25:
            low_q = max(min_q, max_q - 24)

        quantity_choices = [
            SelectChoice(
                label=str(q),
                value=str(q),
                default=(self.pending_quantity == q),
            )
            for q in range(low_q, max_q + 1)
        ]

        val_start = 1 if not self.settings.get("wild_ones", True) else 2
        value_choices = [
            SelectChoice(
                label=f"Value {v}",
                value=str(v),
                emoji=f"die_{v}",
                default=(self.pending_value == v),
            )
            for v in range(val_start, 7)
        ]

        at_max_bid = self._is_max_bid()
        pending_text = ""
        if self.pending_quantity is not None and self.pending_value is not None:
            pending_text = (
                f"**Pending bid:** {self.pending_quantity} × "
                f"{self._die_emoji(ctx, self.pending_value)}"
            )
        elif self.pending_quantity is not None or self.pending_value is not None:
            pending_text = "**Pending bid:** choose both quantity and value."

        if pending_text:
            add_body(container, pending_text)

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
                disabled=at_max_bid,
            )
        )
        row3.add_button(
            Button(
                source="challenge",
                label="Call Liar!",
                style=ButtonStyle.DANGER,
                disabled=self.last_bidder is None,
            )
        )
        row3.add_button(
            Button(
                source="peek",
                label="Peek Hand",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                query=True,
            )
        )
        container.add_action_row(row3)
        return view

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner_seat = summary.get("winner")
        view = LayoutView()
        container = Container()
        winner_name = self.players[winner_seat].mention if winner_seat is not None else "Unknown"
        message_lead(
            container,
            f"{winner_name} wins the match!",
            emoji=ctx.emoji,
            prefix_emoji="success",
        )

        block = history_block(self.history, ctx.emoji, limit=10)
        if block:
            add_body(container, block)
        view.add_container(container)
        return view

    async def parse_replay(self, moves: list[Move], ctx: GameContext) -> list[ReplayFrame]:
        self.dice_counts = {p.seat: self.settings.get("dice_count", 5) for p in self.players}
        self.hands = {}
        self.alive = {p.seat for p in self.players}
        self.current_bid = None
        self.last_bidder = None
        builder = ReplayBuilder(ctx)
        for step in iter_replay(moves, self.players):
            move = step.move
            args = move.args
            if move.source == "round_start":
                self.hands = {int(k): v for k, v in args["hands"].items()}
                self.dice_counts = {int(k): v for k, v in args["dice_counts"].items()}
                self.current_bid = None
                self.last_bidder = None
                builder.add(step, self._round_view_replay(ctx, lead="Dice rolled!"), label="Round Start")
            elif move.source == "bid":
                self.current_bid = (args["quantity"], args["value"])
                self.last_bidder = move.actor_seat
                builder.add(
                    step,
                    self._round_view_replay(
                        ctx,
                        lead=f"Bid submitted by {self.players[move.actor_seat].mention}",
                    ),
                    label="Bid",
                )
            elif move.source == "challenge_resolve":
                self.history = args.get("history", [])
                loser = args["loser"]
                self.dice_counts[loser] -= 1
                if self.dice_counts[loser] == 0:
                    self.alive.discard(loser)
                view = LayoutView()
                container = Container()
                message_lead(container, args["verdict"], emoji=ctx.emoji)
                view.add_container(container)
                builder.add(step, view, label="Challenge")
        return builder.build()

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        move = await run_cpu(choose_move, self, difficulty, seat)
        if move.source == "bid":
            self.pending_quantity = move.args.get("quantity")
            self.pending_value = move.args.get("value")
        return move

    async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
        if source == "peek":
            hand = self.hands.get(seat, [])
            if not hand:
                view = query_panel(
                    ctx,
                    title="Your dice",
                    prefix_emoji="game_liars_dice",
                    body="You have no dice left in this round.",
                )
            else:
                view = query_panel(
                    ctx,
                    title="Your dice",
                    prefix_emoji="game_liars_dice",
                    body=self._format_hand(ctx, hand),
                )
            await ctx.respond_query(view)
            return True
        return await super().handle_query(seat, source, ctx)
