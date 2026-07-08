from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
from strife.games.coup.bot import choose_move
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


class Coup(Game):
    ROLES = ["duke", "assassin", "captain", "ambassador", "contessa"]

    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        # Create deck: 3 of each role
        self.deck = list(self.ROLES * 3)
        self.rng.shuffle(self.deck)

        # Hands: list of active cards for each player
        self.hands: dict[int, list[str]] = {p.seat: [self.deck.pop(), self.deck.pop()] for p in players}
        # Revealed cards: public knowledge
        self.revealed: dict[int, list[str]] = {p.seat: [] for p in players}
        
        # Coins: start at 2
        self.coins: dict[int, int] = {p.seat: 2 for p in players}
        self.alive: set[int] = {p.seat for p in players}
        self.current = self.rng.randint(0, len(players) - 1)
        self.history: list[str] = []

        # Flow phase tracking variables
        self.state_phase = "turn"  # turn, challenge_window, block_window, block_challenge_window, lose_influence, exchange
        self.current_actor: int | None = None
        self.current_action: str | None = None
        self.current_target: int | None = None
        self.exchange_options: dict[int, list[str]] = {}

    def _next_player(self, current: int) -> int:
        n = len(self.players)
        seat = (current + 1) % n
        while seat not in self.alive:
            seat = (seat + 1) % n
        return seat

    def _role_emoji(self, ctx: GameContext, role: str) -> str:
        fallback = {"duke": "👑", "assassin": "🗡️", "captain": "⚓", "ambassador": "💼", "contessa": "🛡️"}.get(role, "🎴")
        return ctx.emoji.get(f"coup_{role}") or fallback

    def _format_hand(self, ctx: GameContext, seat: int, private: bool = False) -> str:
        if private:
            return " ".join(f"{self._role_emoji(ctx, r)} **{r.title()}**" for r in self.hands[seat])
        # Public view shows counts and revealed cards
        cards = ["🎴" for _ in self.hands[seat]]
        rev = [f"{self._role_emoji(ctx, r)} ~~{r.title()}~~" for r in self.revealed[seat]]
        return " ".join(cards + rev)

    async def play(self, ctx: GameContext) -> GameOutcome:
        while len(self.alive) > 1:
            actor = self.current
            self.state_phase = "turn"
            self.current_actor = actor
            self.current_action = None
            self.current_target = None

            # 1. Update public view and request action
            view = self._public_board_view(ctx)
            
            # Send private status view containing player cards
            for seat in self.alive:
                priv_view = LayoutView()
                priv_container = Container()
                add_game_header(
                    priv_container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Your Secret Hand",
                    status=f"Your cards: {self._format_hand(ctx, seat, private=True)} | Coins: {self.coins[seat]}",
                    is_replay=ctx.is_replay,
                )
                priv_view.add_container(priv_container)
                await ctx.send_private(seat, priv_view)

            # If actor has 10+ coins, they MUST Coup
            forced_coup = (self.coins[actor] >= 10)
            sources = {"action_coup", "action_target_select"} if forced_coup else {
                "action_income", "action_foreign_aid", "action_coup",
                "action_tax", "action_assassinate", "action_steal", "action_exchange",
                "action_target_select"
            }

            move = await ctx.request_input(view, actor=actor, sources=sources)

            if move.source == "action_target_select":
                val = move.args.get("value") or (move.args.get("values")[0] if move.args.get("values") else None)
                if val is not None:
                    self.current_target = int(val)
                continue
            
            # Parse action details
            action_type = move.source.split("action_")[1]
            target = move.args.get("target")
            if target is None:
                target = self.current_target
            else:
                target = int(target)

            if action_type in ("coup", "assassinate", "steal"):
                if target is None or target not in self.alive or target == actor:
                    continue  # Invalid target

            self.current_action = action_type
            self.current_target = target

            await ctx.record_action("action_declare", {
                "player": actor,
                "type": action_type,
                "target": target,
            })

            # Check costs
            if action_type == "coup":
                self.coins[actor] -= 7
            elif action_type == "assassinate":
                self.coins[actor] -= 3

            # 2. Challenge Phase (Character actions: tax, assassinate, steal, exchange)
            challenge_card = {
                "tax": "duke",
                "assassinate": "assassin",
                "steal": "captain",
                "exchange": "ambassador",
            }.get(action_type)

            action_blocked = False
            action_failed = False

            if challenge_card is not None:
                self.state_phase = "challenge_window"
                opponents = set(self.alive) - {actor}
                
                # Check for challenges
                react_moves = await ctx.request_inputs(
                    self._public_board_view(ctx, status=f"Waiting for challenges to {self.players[actor].display_name}'s claim..."),
                    actors=opponents,
                    sources={"challenge", "pass"},
                    until="any",
                )

                challenger_seat = None
                react_move = None
                for seat, m in react_moves.items():
                    if m.source == "challenge":
                        challenger_seat = seat
                        react_move = m
                        break
                
                if react_move is None and react_moves:
                    challenger_seat, react_move = next(iter(react_moves.items()))

                if react_move and react_move.source == "challenge":
                    # A challenge occurred!
                    await ctx.record_action("challenge_declare", {
                        "challenger": challenger_seat,
                        "challenged": actor,
                        "card": challenge_card,
                    })

                    if challenge_card in self.hands[actor]:
                        # Actor has the card - they win the challenge!
                        self.history.append(f"{self.players[actor].display_name} successfully proved they have {challenge_card}!")
                        
                        # Swap card
                        self.hands[actor].remove(challenge_card)
                        self.deck.append(challenge_card)
                        self.rng.shuffle(self.deck)
                        self.hands[actor].append(self.deck.pop())

                        # Challenger loses a card
                        await self._lose_influence(ctx, challenger_seat, f"{self.players[challenger_seat].display_name} failed challenge and must lose influence.")
                    else:
                        # Actor lied - they lose the challenge!
                        self.history.append(f"{self.players[actor].display_name} lied about having {challenge_card}!")
                        action_failed = True
                        await self._lose_influence(ctx, actor, f"{self.players[actor].display_name} failed challenge and must lose influence.")

            # 3. Block Phase (if action is blockable and did not fail)
            if not action_failed and action_type in ("foreign_aid", "assassinate", "steal"):
                self.state_phase = "block_window"
                blockers = ({target} if action_type in ("assassinate", "steal") else set(self.alive) - {actor}) & self.alive
                
                block_moves = await ctx.request_inputs(
                    self._public_board_view(ctx, status=f"Waiting for blocks..."),
                    actors=blockers,
                    sources={"block_captain", "block_ambassador", "block_contessa", "block_duke", "pass"},
                    until="any",
                )

                blocker_seat = None
                block_move = None
                for seat, m in block_moves.items():
                    if m.source.startswith("block_"):
                        blocker_seat = seat
                        block_move = m
                        break
                
                if block_move is None and block_moves:
                    blocker_seat, block_move = next(iter(block_moves.items()))

                if block_move and block_move.source.startswith("block_"):
                    claim = block_move.source.split("block_")[1]
                    await ctx.record_action("block_declare", {
                        "blocker": blocker_seat,
                        "action": action_type,
                        "claim": claim,
                    })

                    # Active player can challenge the block
                    self.state_phase = "block_challenge_window"
                    challenge_react = await ctx.request_input(
                        self._public_board_view(ctx, status=f"{self.players[blocker_seat].display_name} blocks with {claim.title()}. Challenge?"),
                        actor=actor,
                        sources={"challenge", "pass"},
                    )

                    if challenge_react.source == "challenge":
                        await ctx.record_action("block_challenge", {
                            "challenger": actor,
                            "blocker": blocker_seat,
                            "claim": claim,
                        })

                        # Blocker must show claim card
                        if claim in self.hands[blocker_seat]:
                            # Blocker tells truth - block succeeds, challenger (actor) loses card
                            self.history.append(f"{self.players[blocker_seat].display_name} proved they have {claim}!")
                            action_blocked = True
                            
                            self.hands[blocker_seat].remove(claim)
                            self.deck.append(claim)
                            self.rng.shuffle(self.deck)
                            self.hands[blocker_seat].append(self.deck.pop())

                            await self._lose_influence(ctx, actor, f"{self.players[actor].display_name} failed block challenge and must lose influence.")
                        else:
                            # Blocker lied - block fails, blocker loses card
                            self.history.append(f"{self.players[blocker_seat].display_name} lied about having {claim}!")
                            await self._lose_influence(ctx, blocker_seat, f"{self.players[blocker_seat].display_name} failed block challenge.")
                    else:
                        action_blocked = True

            # 4. Resolve Action Effects
            if not action_failed and not action_blocked:
                if action_type == "income":
                    self.coins[actor] += 1
                    self.history.append(f"{self.players[actor].display_name} took Income.")
                elif action_type == "foreign_aid":
                    self.coins[actor] += 2
                    self.history.append(f"{self.players[actor].display_name} took Foreign Aid.")
                elif action_type == "tax":
                    self.coins[actor] += 3
                    self.history.append(f"{self.players[actor].display_name} taxed the Treasury (+3 coins).")
                elif action_type == "coup":
                    self.history.append(f"{self.players[actor].display_name} staged a Coup on {self.players[target].display_name}!")
                    await self._lose_influence(ctx, target, f"Coup target {self.players[target].display_name} must lose influence.")
                elif action_type == "assassinate":
                    self.history.append(f"{self.players[actor].display_name} assassinated {self.players[target].display_name}!")
                    await self._lose_influence(ctx, target, f"Assassination target {self.players[target].display_name} must lose influence.")
                elif action_type == "steal":
                    stolen = min(2, self.coins[target])
                    self.coins[actor] += stolen
                    self.coins[target] -= stolen
                    self.history.append(f"{self.players[actor].display_name} stole {stolen} coins from {self.players[target].display_name}.")
                elif action_type == "exchange":
                    self.state_phase = "exchange"
                    # Ambassador exchange
                    drawn = [self.deck.pop(), self.deck.pop()]
                    self.exchange_options[actor] = list(self.hands[actor] + drawn)
                    
                    choices = [
                        SelectChoice(label=role.title(), value=role, emoji=f"coup_{role}")
                        for role in self.exchange_options[actor]
                    ]
                    
                    exchange_view = LayoutView()
                    exchange_container = Container()
                    add_game_header(
                        exchange_container,
                        ctx.emoji,
                        game_key=self.metadata.key,
                        game_name=self.metadata.name,
                        title="Ambassador Card Exchange",
                        status=f"Select {len(self.hands[actor])} card(s) to keep",
                        is_replay=ctx.is_replay,
                    )
                    row = ActionRow()
                    row.add_select(
                        Select(
                            source="exchange_select",
                            placeholder="Select cards to keep",
                            choices=choices,
                            min_values=len(self.hands[actor]),
                            max_values=len(self.hands[actor]),
                        )
                    )
                    exchange_container.add_action_row(row)
                    exchange_view.add_container(exchange_container)

                    # Prompt privately for keeping
                    keep_move = await ctx.request_input(exchange_view, actor=actor, sources={"exchange_select"})
                    keep_list = keep_move.args.get("values", [])
                    if not keep_list and keep_move.args.get("value"):
                        keep_list = [keep_move.args.get("value")]
                    if not keep_list and keep_move.args.get("keep"):
                        keep_list = keep_move.args.get("keep")

                    # Update hand
                    for card in keep_list:
                        if card in self.exchange_options[actor]:
                            self.exchange_options[actor].remove(card)
                    
                    # Return leftovers to deck
                    self.hands[actor] = keep_list
                    self.deck.extend(self.exchange_options[actor])
                    self.rng.shuffle(self.deck)
                    self.history.append(f"{self.players[actor].display_name} exchanged cards with the Deck.")
                    
                    await ctx.record_action("exchange_resolve", {
                        "player": actor,
                        "keep": keep_list,
                    })

            self.current = self._next_player(actor)

        # Game over, compile outcome
        winner = next(iter(self.alive))
        winner_mention = str(self.players[winner])
        results = {p.seat: "win" if p.seat == winner else "loss" for p in self.players}
        player_descriptions = {
            p.seat: "Won the coup!" if p.seat == winner else "Influence eliminated."
            for p in self.players
        }

        return GameOutcome(
            results=results,
            summary={"winner": winner, "history": list(self.history)},
            description=f"{winner_mention} won!",
            player_descriptions=player_descriptions,
        )

    async def _lose_influence(self, ctx: GameContext, seat: int, status_message: str) -> None:
        self.state_phase = "lose_influence"
        cards = self.hands[seat]
        if not cards:
            return

        if len(cards) == 1:
            # Forced choice
            lost_card = cards.pop()
            self.revealed[seat].append(lost_card)
            self.history.append(f"{self.players[seat].display_name} revealed their last card: {lost_card.title()}.")
            self.alive.discard(seat)
            await ctx.record_action("lose_influence_resolve", {
                "player": seat,
                "card": lost_card,
            })
            return

        choices = [
            SelectChoice(label=role.title(), value=role, emoji=f"coup_{role}")
            for role in cards
        ]
        
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title="Lose Influence",
            status=status_message,
            is_replay=ctx.is_replay,
        )
        row = ActionRow()
        row.add_select(
            Select(
                source="lose_influence_select",
                placeholder="Choose a card to lose",
                choices=choices,
            )
        )
        container.add_action_row(row)
        view.add_container(container)

        # Request selection
        move = await ctx.request_input(view, actor=seat, sources={"lose_influence_select"})
        lost_card = move.args.get("value") or (move.args.get("values")[0] if move.args.get("values") else (move.args.get("card") or cards[0]))
        
        if lost_card in cards:
            self.hands[seat].remove(lost_card)
            self.revealed[seat].append(lost_card)
        else:
            lost_card = self.hands[seat].pop()
            self.revealed[seat].append(lost_card)

        self.history.append(f"{self.players[seat].display_name} revealed a card: {lost_card.title()}.")
        
        await ctx.record_action("lose_influence_resolve", {
            "player": seat,
            "card": lost_card,
        })

    def _public_board_view(self, ctx: GameContext, status: str | None = None) -> LayoutView:
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title=f"Coup - Turn of {self.players[self.current].display_name}",
            status=status or f"Coins: {self.coins[self.current]}",
            is_replay=ctx.is_replay,
        )

        table_text = "**Roster and Influence:**\n"
        for p in self.players:
            alive_status = self._format_hand(ctx, p.seat)
            table_text += f"• {p.display_name}: {alive_status} (💰 {self.coins[p.seat]} coins)\n"

        if self.history:
            table_text += "\n**Recent Logs:**\n" + "\n".join(f"• {item}" for item in self.history[-5:])

        container.add_text(TextDisplay(table_text))

        # Show target selects if appropriate
        targets = [
            SelectChoice(label=p.display_name, value=str(p.seat), default=(p.seat == self.current_target))
            for p in self.players
            if p.seat in self.alive and p.seat != self.current
        ]

        if self.state_phase == "turn":
            # Action Dropdown for targets
            if targets:
                row_target = ActionRow()
                row_target.add_select(
                    Select(
                        source="action_target_select",
                        placeholder="Choose a target (Coup/Assassinate/Steal)",
                        choices=targets,
                    )
                )
                container.add_action_row(row_target)

            # Normal Action Buttons
            row_actions1 = ActionRow()
            row_actions1.add_button(Button(source="action_income", label="Income (+1)", style=ButtonStyle.SECONDARY))
            row_actions1.add_button(Button(source="action_foreign_aid", label="Foreign Aid (+2)", style=ButtonStyle.SECONDARY))
            row_actions1.add_button(Button(source="action_coup", label="Coup (-7)", style=ButtonStyle.DANGER, disabled=(self.coins[self.current] < 7)))
            row_actions1.add_button(Button(source="action_tax", label="Tax (+3)", style=ButtonStyle.PRIMARY))
            container.add_action_row(row_actions1)

            row_actions2 = ActionRow()
            row_actions2.add_button(Button(source="action_assassinate", label="Assassinate (-3)", style=ButtonStyle.DANGER, disabled=(self.coins[self.current] < 3)))
            row_actions2.add_button(Button(source="action_steal", label="Steal (Captain)", style=ButtonStyle.PRIMARY))
            row_actions2.add_button(Button(source="action_exchange", label="Exchange (Ambassador)", style=ButtonStyle.PRIMARY))
            container.add_action_row(row_actions2)

        elif self.state_phase in ("challenge_window", "block_challenge_window"):
            row = ActionRow()
            row.add_button(Button(source="challenge", label="Challenge Claim", style=ButtonStyle.DANGER))
            row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY))
            container.add_action_row(row)

        elif self.state_phase == "block_window":
            row = ActionRow()
            # Depending on action blockable cards
            if self.current_action == "steal":
                # Blocker can claim captain or ambassador
                row.add_button(Button(source="block_captain", label="Block: Captain", style=ButtonStyle.PRIMARY))
                row.add_button(Button(source="block_ambassador", label="Block: Ambassador", style=ButtonStyle.PRIMARY))
            elif self.current_action == "assassinate":
                row.add_button(Button(source="block_contessa", label="Block: Contessa", style=ButtonStyle.PRIMARY))
            elif self.current_action == "foreign_aid":
                row.add_button(Button(source="block_duke", label="Block: Duke", style=ButtonStyle.PRIMARY))
            
            row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY))
            container.add_action_row(row)

        row_peek = ActionRow()
        row_peek.add_button(Button(source="peek", label="Peek Cards", emoji="peek", style=ButtonStyle.SECONDARY))
        container.add_action_row(row_peek)

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
            title="Coup - Game Over!",
            status=f"{winner_name} wins the match!",
            status_emoji="success",
            is_replay=ctx.is_replay,
        )

        history_text = "**Match History:**\n" + "\n".join(f"• {item}" for item in self.history)
        container.add_text(TextDisplay(history_text))
        view.add_container(container)
        return view

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        # Reset state to setup
        self.deck = list(self.ROLES * 3)
        self.hands = {p.seat: [] for p in self.players}
        self.revealed = {p.seat: [] for p in self.players}
        self.coins = {p.seat: 2 for p in self.players}
        self.alive = {p.seat for p in self.players}
        self.history = []

        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        for move in moves:
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

            # Simplistic replay reproduction based on actions recorded
            if move.source == "action_declare":
                self.current_actor = move.actor_seat
                self.current_action = move.arguments["type"]
                self.current_target = move.arguments["target"]
                
                # Check costs
                if self.current_action == "coup":
                    self.coins[self.current_actor] -= 7
                elif self.current_action == "assassinate":
                    self.coins[self.current_actor] -= 3

                view = self._public_board_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Action (Turn {len(frames)+1})",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "lose_influence_resolve":
                p = move.arguments["player"]
                card = move.arguments["card"]
                if card in self.hands[p]:
                    self.hands[p].remove(card)
                self.revealed[p].append(card)
                if not self.hands[p]:
                    self.alive.discard(p)

                view = self._public_board_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Influence Lost (Turn {len(frames)+1})",
                    actor_seat=p,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

        return frames

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        move = await asyncio.to_thread(choose_move, self, difficulty, seat)
        # Adapt move inputs back to the button name clicks
        if move.source == "action":
            action_type = move.args.get("type", "income")
            source = f"action_{action_type}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "block":
            claim = move.args.get("claim", "captain")
            source = f"block_{claim}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "lose_card":
            # Maps to Select value selection in lose_influence
            return Move(actor_seat=seat, source="lose_influence_select", args=move.args)
        elif move.source == "exchange_keep":
            return Move(actor_seat=seat, source="exchange_select", args=move.args)
        return move

    def peek_info(self, seat: int, ctx: GameContext) -> str:
        cards = self.hands.get(seat, [])
        if not cards:
            return "You have no active cards left."
        return f"🎴 **Your Secret Cards:** {self._format_hand(ctx, seat, private=True)} | Coins: {self.coins.get(seat, 0)}"
