from __future__ import annotations

import asyncio
from collections import Counter

from strife.engine.context import GameContext
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
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

    async def play(self, ctx: GameContext) -> GameOutcome:
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
        return self._finish(winner)

    async def _send_role_dms(self, ctx: GameContext) -> None:
        for player in self.players:
            role = self.role[player.seat]
            instructions = next((r.instructions for r in self.metadata.roles if r.key == role), "")
            view = LayoutView()
            container = Container()
            container.add_text(TextDisplay(markdown_content=f"### Your role: {role.title()}", size_style=TextSize.HEADER))
            container.add_text(TextDisplay(instructions))
            if role == "mafia":
                teammates = [p.display_name for p in self.players if self.role[p.seat] == "mafia" and p.seat != player.seat]
                if teammates:
                    container.add_separator()
                    container.add_text(TextDisplay(f"Your mafia teammates: {', '.join(teammates)}"))
            view.add_container(container)
            await ctx.send_private(player.seat, view)

    async def _night(self, ctx: GameContext) -> None:
        self._phase = "night"
        acting = sorted(seat for seat in self.alive if self.role[seat] in {"mafia", "doctor", "detective"})
        await ctx.update(self._public_view(f"Night {self.day} falls..."))
        moves: dict[int, Move] = {}
        for seat in acting:
            role = self.role[seat]
            choices = [
                SelectChoice(label=self.players[s].display_name, value=str(s))
                for s in sorted(self.alive)
                if not (role == "mafia" and self.role[s] == "mafia")
            ]
            private = LayoutView()
            private.add_container(
                Container(
                    children=[
                        TextDisplay(f"Night action for your role: **{role}**"),
                    ]
                )
            )
            row = ActionRow()
            source = {"mafia": "kill", "doctor": "protect", "detective": "investigate"}[role]
            row.add_select(Select(source=source, placeholder="Choose a target", choices=choices))
            private.add_action_row(row)
            await ctx.send_private(seat, private)
            move = await ctx.request_input(private, actor=seat, sources={source})
            if move.args.get("value"):
                move.args["target"] = move.args["value"]
            moves[seat] = move

        kills = [int(m.args.get("target")) for seat, m in moves.items() if self.role[seat] == "mafia" and m.args.get("target") is not None]
        protects = [int(m.args.get("target")) for seat, m in moves.items() if self.role[seat] == "doctor" and m.args.get("target") is not None]
        victim = None
        if kills:
            counts = Counter(kills)
            victim, _ = counts.most_common(1)[0]
            if protects and victim in protects:
                victim = None
        if victim is not None and victim in self.alive:
            self.remove_player(victim)
            self.history.append(f"Night {self.day}: {self.players[victim].display_name} was eliminated.")

        for seat, move in moves.items():
            if self.role[seat] == "detective" and move.args.get("target") is not None:
                target = int(move.args["target"])
                alignment = "mafia" if self.role.get(target) == "mafia" else "town"
                reveal = LayoutView()
                reveal.add_container(Container(children=[TextDisplay(f"Investigation: {self.players[target].display_name} is {alignment}.")]))
                await ctx.send_private(seat, reveal)

    async def _day(self, ctx: GameContext) -> None:
        self._phase = "day"
        day_view = self._day_view()
        votes = await ctx.request_inputs(day_view, actors=set(self.alive), sources={"vote"}, until="all")
        tally: Counter[int] = Counter()
        for seat, move in votes.items():
            target = move.args.get("target")
            if target == "skip":
                continue
            tally[int(target)] += 1
        if tally:
            top = tally.most_common()
            if len(top) == 1 or top[0][1] > top[1][1]:
                lynched = top[0][0]
                self.remove_player(lynched)
                self.history.append(f"Day {self.day}: {self.players[lynched].display_name} was lynched.")

    def _public_view(self, text: str) -> LayoutView:
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(markdown_content="### Mafia", size_style=TextSize.HEADER))
        container.add_text(TextDisplay(text))
        alive = ", ".join(self.players[s].display_name for s in sorted(self.alive))
        container.add_text(TextDisplay(f"**Alive:** {alive}"))
        if self.history:
            container.add_separator()
            container.add_text(TextDisplay("\n".join(self.history[-5:])))
        view.add_container(container)
        return view

    def _day_view(self) -> LayoutView:
        view = self._public_view(f"Day {self.day} discussion and vote")
        row = ActionRow()
        choices = [SelectChoice(label=self.players[s].display_name, value=str(s)) for s in sorted(self.alive)]
        choices.append(SelectChoice(label="Skip", value="skip"))
        row.add_select(Select(source="vote", placeholder="Cast your vote", choices=choices))
        view.add_action_row(row)
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
        view_lines = [f"{p.display_name}: {self.role[p.seat]}" for p in self.players]
        return GameOutcome(
            results=results,
            summary={"winning_faction": winner, "roles": role_map, "reveal": "\n".join(view_lines)},
        )

    def remove_player(self, seat: int) -> None:
        self.alive.discard(seat)

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        source, args = await asyncio.to_thread(choose_mafia_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=source, args=args)
