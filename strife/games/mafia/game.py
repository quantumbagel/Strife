from __future__ import annotations

import asyncio
from collections import Counter

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import MoveRecord
from strife.games.mafia.bot import choose_mafia_move
from strife.games.mafia.roles import compose_roles
from strife.presentation.components import (
    ActionRow,
    Container,
    LayoutView,
    Select,
    SelectChoice,
    TextDisplay,
    TextSize,
)
from strife.presentation.roster import member_line, player_mention


class Mafia(Game):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        role_list = compose_roles(self.metadata, len(players), settings, rng)
        self.role: dict[int, str] = {p.seat: role_list[i] for i, p in enumerate(players)}
        for player in players:
            player.role_key = self.role[player.seat]
        self.alive: set[int] = {p.seat for p in players}
        self.day = 0
        self.history: list[str] = []
        self._phase = "night"

    def _name(self, seat: int) -> str:
        player = self.players[seat]
        return player_mention(
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
        )

    async def play(self, ctx: GameContext) -> GameOutcome:
        await ctx.record_action("roles_assigned", {"roles": self.role})
        await self._send_role_dms(ctx)
        winner = None
        while winner is None:
            self.day += 1
            await self._night(ctx)
            winner = self._winner()
            if winner:
                break
            await self._day(ctx)
            winner = self._winner()
        await ctx.record_action("game_end", {"winning_faction": winner, "roles": self.role})
        return self._finish(winner)

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner = summary.get("winning_faction", "unknown")
        forward = ctx.emoji.get("forward")
        view = LayoutView()
        container = Container()
        container.add_text(
            TextDisplay(
                markdown_content=f"### {ctx.emoji.get_game_emoji('mafia')} {forward} Game Over",
                size_style=TextSize.HEADER,
            )
        )
        container.add_text(
            TextDisplay(
                markdown_content=f"{ctx.emoji.get('success')} **{winner.title()} wins!**",
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()
        lines = []
        for player in self.players:
            role = self.role[player.seat]
            name = member_line(
                ctx.emoji,
                user_id=player.user_id,
                display_name=player.display_name,
                is_bot=player.is_bot,
                bot_difficulty=player.bot_difficulty,
            )
            lines.append(f"• {name} {forward} **{role.title()}**")
        container.add_text(TextDisplay(markdown_content="\n".join(lines)))
        view.add_container(container)
        return view

    async def _send_role_dms(self, ctx: GameContext) -> None:
        for player in self.players:
            role = self.role[player.seat]
            instructions = next((r.instructions for r in self.metadata.roles if r.key == role), "")
            view = LayoutView()
            container = Container()
            container.add_text(
                TextDisplay(
                    markdown_content=f"### Your role: {role.title()}",
                    size_style=TextSize.HEADER,
                )
            )
            container.add_text(TextDisplay(instructions))
            if role == "mafia":
                teammates = [
                    self._name(p.seat)
                    for p in self.players
                    if self.role[p.seat] == "mafia" and p.seat != player.seat
                ]
                if teammates:
                    container.add_separator()
                    container.add_text(TextDisplay(f"Your mafia teammates: {', '.join(teammates)}"))
            view.add_container(container)
            await ctx.send_private(player.seat, view)

    async def _night(self, ctx: GameContext) -> None:
        self._phase = "night"
        acting = sorted(seat for seat in self.alive if self.role[seat] in {"mafia", "doctor", "detective"})
        await ctx.update(self._public_view(ctx, f"Night {self.day} falls..."))
        await ctx.record_action("night_start", {"day": self.day})
        moves: dict[int, Move] = {}
        for seat in acting:
            role = self.role[seat]
            choices = [
                SelectChoice(label=self.players[s].display_name, value=str(s))
                for s in sorted(self.alive)
                if not (role == "mafia" and self.role[s] == "mafia")
            ]
            private = LayoutView()
            container = Container(
                children=[TextDisplay(f"Night action for your role: **{role}**")]
            )
            row = ActionRow()
            source = {"mafia": "kill", "doctor": "protect", "detective": "investigate"}[role]
            row.add_select(Select(source=source, placeholder="Choose a target", choices=choices))
            container.add_action_row(row)
            private.add_container(container)
            await ctx.send_private(seat, private)
            move = await ctx.request_input(private, actor=seat, sources={source})
            if move.args.get("value"):
                move.args["target"] = move.args["value"]
            moves[seat] = move

        kills = [
            int(m.args.get("target"))
            for seat, m in moves.items()
            if self.role[seat] == "mafia" and m.args.get("target") is not None
        ]
        protects = [
            int(m.args.get("target"))
            for seat, m in moves.items()
            if self.role[seat] == "doctor" and m.args.get("target") is not None
        ]
        victim = None
        if kills:
            counts = Counter(kills)
            victim, _ = counts.most_common(1)[0]
            if protects and victim in protects:
                victim = None
        if victim is not None and victim in self.alive:
            self.remove_player(victim)
            self.history.append(f"Night {self.day}: {self._name(victim)} was eliminated.")
        await ctx.record_action("night_outcome", {
            "victim": victim,
            "history": list(self.history)
        })

        for seat, move in moves.items():
            if self.role[seat] == "detective" and move.args.get("target") is not None:
                target = int(move.args["target"])
                alignment = "mafia" if self.role.get(target) == "mafia" else "town"
                reveal = LayoutView()
                reveal.add_container(
                    Container(
                        children=[
                            TextDisplay(
                                f"Investigation: {self._name(target)} is {alignment}."
                            )
                        ]
                    )
                )
                await ctx.send_private(seat, reveal)
                await ctx.record_action("detective_reveal", {
                    "detective": seat,
                    "target": target,
                    "alignment": alignment
                })

    async def _day(self, ctx: GameContext) -> None:
        self._phase = "day"
        day_view = self._day_view(ctx)
        votes = await ctx.request_inputs(day_view, actors=set(self.alive), sources={"vote"}, until="all")
        tally: Counter[int] = Counter()
        for seat, move in votes.items():
            target = move.args.get("target")
            if target == "skip":
                continue
            tally[int(target)] += 1
        lynched = None
        if tally:
            top = tally.most_common()
            if len(top) == 1 or top[0][1] > top[1][1]:
                lynched = top[0][0]
                self.remove_player(lynched)
                self.history.append(f"Day {self.day}: {self._name(lynched)} was lynched.")
        await ctx.record_action("day_outcome", {
            "lynched": lynched,
            "history": list(self.history),
            "votes": {seat: m.args.get("target") for seat, m in votes.items()}
        })

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        def _get_name(seat: int) -> str:
            player = self.players[seat]
            return player_mention(
                user_id=player.user_id,
                display_name=player.display_name,
                is_bot=player.is_bot,
            )

        roles = {}
        alive = set(p.seat for p in self.players)
        history = []
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
                            "display_name": p.display_name,
                            "type": "bot_takeover",
                            "reason": move.arguments.get("replace_reason", "timeout"),
                        }
            elif move.source == "forfeit":
                for p in self.players:
                    if p.seat == move.actor_seat:
                        takeover_info = {
                            "display_name": p.display_name,
                            "type": "removal",
                            "reason": move.arguments.get("reason", "forfeit"),
                        }
                if move.actor_seat is not None:
                    alive.discard(move.actor_seat)
                    
            if move.source == "roles_assigned":
                roles = {int(k): v for k, v in move.arguments["roles"].items()}
                for p in self.players:
                    p.role_key = roles.get(p.seat)
                
                setup_view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content=f"### {ctx.emoji.get_game_emoji('mafia')} Game Started: Roles Setup",
                        size_style=TextSize.HEADER,
                    )
                )
                role_lines = []
                for p in self.players:
                    role = roles.get(p.seat, "unknown")
                    role_lines.append(f"• {_get_name(p.seat)} ➔ **{role.title()}**")
                container.add_text(TextDisplay(markdown_content="\n".join(role_lines)))
                setup_view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Setup",
                        actor_seat=None,
                        view=clone_and_disable(setup_view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source == "night_start":
                day_num = move.arguments["day"]
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content=f"### Night {day_num} Falls...",
                        size_style=TextSize.HEADER,
                    )
                )
                alive_str = ", ".join(_get_name(s) for s in sorted(alive))
                container.add_text(TextDisplay(f"**Alive:** {alive_str}"))
                if history:
                    container.add_separator()
                    container.add_text(TextDisplay("\n".join(history[-5:])))
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label=f"Night {day_num}",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source in ("kill", "protect", "investigate"):
                actor_seat = move.actor_seat
                role = roles.get(actor_seat, "unknown") if actor_seat is not None else "unknown"
                target_seat = int(move.arguments.get("target")) if move.arguments.get("target") is not None else None
                target_str = _get_name(target_seat) if target_seat is not None else "no one"
                
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content=f"### Night Action: {role.title()}",
                        size_style=TextSize.HEADER,
                    )
                )
                actor_str = _get_name(actor_seat) if actor_seat is not None else "Unknown"
                container.add_text(
                    TextDisplay(
                        markdown_content=f"{actor_str} ({role.title()}) chose to **{move.source}** {target_str}.",
                        size_style=TextSize.BODY,
                    )
                )
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label=f"Night Action",
                        actor_seat=actor_seat,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source == "detective_reveal":
                detective_seat = move.arguments["detective"]
                target_seat = move.arguments["target"]
                alignment = move.arguments["alignment"]
                
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content="### Detective Investigation Reveal",
                        size_style=TextSize.HEADER,
                    )
                )
                container.add_text(
                    TextDisplay(
                        markdown_content=f"{_get_name(detective_seat)} investigated {_get_name(target_seat)} and found them to be **{alignment.upper()}**.",
                        size_style=TextSize.BODY,
                    )
                )
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Investigation",
                        actor_seat=detective_seat,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source == "night_outcome":
                victim = move.arguments.get("victim")
                history = move.arguments.get("history", [])
                if victim is not None:
                    alive.discard(int(victim))
                
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content="### Morning Results",
                        size_style=TextSize.HEADER,
                    )
                )
                if victim is not None:
                    outcome_msg = f"🌅 **{_get_name(int(victim))} was eliminated during the night.**"
                else:
                    outcome_msg = "🌅 **No one was eliminated during the night.**"
                container.add_text(TextDisplay(outcome_msg))
                container.add_separator()
                alive_str = ", ".join(_get_name(s) for s in sorted(alive))
                container.add_text(TextDisplay(f"**Alive:** {alive_str}"))
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Morning",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source == "day_outcome":
                lynched = move.arguments.get("lynched")
                history = move.arguments.get("history", [])
                votes_cast = move.arguments.get("votes", {})
                if lynched is not None:
                    alive.discard(int(lynched))
                    
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content="### Day Voting Results",
                        size_style=TextSize.HEADER,
                    )
                )
                
                vote_lines = []
                for voter_str, target_str in votes_cast.items():
                    voter_seat = int(voter_str)
                    target_seat = int(target_str) if target_str != "skip" else None
                    target_display = _get_name(target_seat) if target_seat is not None else "Skip"
                    vote_lines.append(f"• {_get_name(voter_seat)} voted for: **{target_display}**")
                if vote_lines:
                    container.add_text(TextDisplay("\n".join(vote_lines)))
                    container.add_separator()
                    
                if lynched is not None:
                    outcome_msg = f"⚖️ **{_get_name(int(lynched))} was lynched by popular vote.**"
                else:
                    outcome_msg = "⚖️ **The vote was skipped or tied. No one was lynched.**"
                container.add_text(TextDisplay(outcome_msg))
                container.add_separator()
                alive_str = ", ".join(_get_name(s) for s in sorted(alive))
                container.add_text(TextDisplay(f"**Alive:** {alive_str}"))
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Lynch Vote",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
            elif move.source == "game_end":
                winning_faction = move.arguments.get("winning_faction", "unknown")
                view = LayoutView()
                container = Container()
                container.add_text(
                    TextDisplay(
                        markdown_content=f"### {ctx.emoji.get_game_emoji('mafia')} Game Over",
                        size_style=TextSize.HEADER,
                    )
                )
                container.add_text(
                    TextDisplay(
                        markdown_content=f"🏆 **{winning_faction.upper()} wins!**",
                        size_style=TextSize.BODY,
                    )
                )
                container.add_separator()
                role_lines = []
                for p in self.players:
                    role = roles.get(p.seat, "unknown")
                    role_lines.append(f"• {_get_name(p.seat)} ➔ **{role.title()}**")
                container.add_text(TextDisplay(markdown_content="\n".join(role_lines)))
                view.add_container(container)
                
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Game Over",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                    )
                )
                
        return frames

    def _public_view(self, ctx: GameContext, text: str) -> LayoutView:
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(text))
        alive = ", ".join(self._name(s) for s in sorted(self.alive))
        container.add_text(TextDisplay(f"**Alive:** {alive}"))
        if self.history:
            container.add_separator()
            container.add_text(TextDisplay("\n".join(self.history[-5:])))
        view.add_container(container)
        return view

    def _day_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(f"Day {self.day} discussion and vote"))
        alive = ", ".join(self._name(s) for s in sorted(self.alive))
        container.add_text(TextDisplay(f"**Alive:** {alive}"))
        if self.history:
            container.add_separator()
            container.add_text(TextDisplay("\n".join(self.history[-5:])))
        row = ActionRow()
        choices = [
            SelectChoice(label=self.players[s].display_name, value=str(s))
            for s in sorted(self.alive)
        ]
        choices.append(SelectChoice(label="Skip", value="skip"))
        row.add_select(Select(source="vote", placeholder="Cast your vote", choices=choices))
        container.add_action_row(row)
        view.add_container(container)
        return view

    def _winner(self) -> str | None:
        mafia_alive = sum(1 for s in self.alive if self.role[s] == "mafia")
        town_alive = sum(1 for s in self.alive if self.role[s] != "mafia")
        if mafia_alive == 0:
            return "town"
        if mafia_alive >= town_alive:
            return "mafia"
        return None

    def _finish(self, winner: str) -> GameOutcome:
        results = {}
        for player in self.players:
            if self.role[player.seat] == "mafia":
                results[player.seat] = "win" if winner == "mafia" else "loss"
            else:
                results[player.seat] = "win" if winner == "town" else "loss"
        role_map = {player.seat: self.role[player.seat] for player in self.players}
        return GameOutcome(
            results=results,
            summary={"winning_faction": winner, "roles": role_map},
        )

    def remove_player(self, seat: int) -> None:
        self.alive.discard(seat)

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        source, args = await asyncio.to_thread(choose_mafia_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=source, args=args)
