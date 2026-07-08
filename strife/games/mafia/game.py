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
)
from strife.presentation.game_frame import add_game_header
from strife.presentation.roster import member_line, player_mention


class Mafia(Game):
    _ROLE_EMOJI = {
        "mafia": "mafia_werewolf",
        "villager": "mafia_villager",
        "doctor": "enable_doctor",
        "detective": "enable_detective",
    }

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
        self.death_reason: dict[int, str] = {}

    def _name(self, seat: int) -> str:
        player = self.players[seat]
        return player_mention(
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
        )

    def _role_emoji(self, ctx: GameContext, role: str) -> str:
        return ctx.emoji.get(self._ROLE_EMOJI.get(role, "user"))

    def _alive_roster(self, ctx: GameContext, alive: set[int] | None = None) -> str:
        seats = sorted(alive if alive is not None else self.alive)
        lines = [
            member_line(
                ctx.emoji,
                user_id=self.players[s].user_id,
                display_name=self.players[s].display_name,
                is_bot=self.players[s].is_bot,
                bot_difficulty=self.players[s].bot_difficulty,
            )
            for s in seats
        ]
        return f"**Alive**\n" + ("\n".join(lines) if lines else "_None_")

    def _history_block(self, ctx: GameContext, history: list[str] | None = None) -> str | None:
        entries = (history if history is not None else self.history)[-5:]
        if not entries:
            return None
        bullet = ctx.emoji.get("bullet")
        return "**Recent events**\n" + "\n".join(f"{bullet} {entry}" for entry in entries)

    def _game_over_view(self, ctx: GameContext, winner: str, roles: dict[int, str] | None = None) -> LayoutView:
        role_map = roles if roles is not None else self.role
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title="Game Over",
            status=f"{winner.title()} wins!",
            status_emoji="success",
        )
        forward = ctx.emoji.get("forward")
        lines = []
        for player in self.players:
            role = role_map.get(player.seat, "unknown")
            role_emoji = self._role_emoji(ctx, role)
            name = member_line(
                ctx.emoji,
                user_id=player.user_id,
                display_name=player.display_name,
                is_bot=player.is_bot,
                bot_difficulty=player.bot_difficulty,
            )
            lines.append(f"{ctx.emoji.get('bullet')} {name} {forward} {role_emoji} **{role.title()}**")
        container.add_text(TextDisplay(markdown_content="\n".join(lines)))
        view.add_container(container)
        return view

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
        return self._game_over_view(ctx, winner)

    def _normalize_target(self, move: Move) -> str | None:
        target = move.args.get("target")
        if target is None and move.args.get("value") is not None:
            target = move.args["value"]
            move.args["target"] = target
        return target

    def _parse_alive_target(self, raw: str | None) -> int | None:
        if raw is None or raw == "skip":
            return None
        if not isinstance(raw, str) or not raw.isdigit():
            return None
        seat = int(raw)
        if seat not in self.alive:
            return None
        return seat

    async def _send_role_dms(self, ctx: GameContext) -> None:
        async def send_one(player: Player) -> None:
            role = self.role[player.seat]
            role_emoji = self._role_emoji(ctx, role)
            instructions = next((r.instructions for r in self.metadata.roles if r.key == role), "")
            view = LayoutView()
            container = Container()
            add_game_header(
                container,
                ctx.emoji,
                game_key=self.metadata.key,
                game_name=self.metadata.name,
                title=f"Your Role: {role.title()}",
                status=instructions,
            )
            if role == "mafia":
                teammates = [
                    self._name(p.seat)
                    for p in self.players
                    if self.role[p.seat] == "mafia" and p.seat != player.seat
                ]
                if teammates:
                    container.add_separator()
                    container.add_text(
                        TextDisplay(
                            markdown_content=(
                                f"{role_emoji} **Mafia teammates:** {', '.join(teammates)}"
                            )
                        )
                    )
            view.add_container(container)
            await ctx.send_private(player.seat, view)

        await asyncio.gather(*(send_one(player) for player in self.players))

    async def _night(self, ctx: GameContext) -> None:
        self._phase = "night"
        acting = sorted(seat for seat in self.alive if self.role[seat] in {"mafia", "doctor", "detective"})
        await ctx.update(
            self._public_view(
                ctx,
                title=f"Night {self.day}",
                status="Night falls across the town...",
                status_emoji="timer",
            )
        )
        await ctx.record_action("night_start", {"day": self.day})
        source_map = {"mafia": "kill", "doctor": "protect", "detective": "investigate"}
        private_views: dict[int, LayoutView] = {}
        per_seat_sources: dict[int, set[str]] = {}
        for seat in acting:
            role = self.role[seat]
            choices = [
                SelectChoice(label=self.players[s].display_name, value=str(s))
                for s in sorted(self.alive)
                if not (role == "mafia" and self.role[s] == "mafia")
            ]
            private = LayoutView()
            container = Container()
            add_game_header(
                container,
                ctx.emoji,
                game_key=self.metadata.key,
                game_name=self.metadata.name,
                title=f"Night {self.day} Action",
                status=f"Choose a target as **{role.title()}**",
                status_emoji="loading",
            )
            row = ActionRow()
            source = source_map[role]
            row.add_select(Select(source=source, placeholder="Choose a target", choices=choices))
            container.add_action_row(row)
            private.add_container(container)
            private_views[seat] = private
            per_seat_sources[seat] = {source}

        await asyncio.gather(
            *(ctx.send_private(seat, private_views[seat]) for seat in acting)
        )
        public = self._public_view(
            ctx,
            title=f"Night {self.day}",
            status="Waiting for night actions...",
            status_emoji="loading",
        )
        moves = await ctx.request_inputs(
            public,
            actors=set(acting),
            sources=None,
            per_seat_sources=per_seat_sources,
            until="all",
        )
        for move in moves.values():
            self._normalize_target(move)

        kills = [
            seat_target
            for seat, m in moves.items()
            if self.role[seat] == "mafia"
            for seat_target in [self._parse_alive_target(self._normalize_target(m))]
            if seat_target is not None
        ]
        protects = [
            seat_target
            for seat, m in moves.items()
            if self.role[seat] == "doctor"
            for seat_target in [self._parse_alive_target(self._normalize_target(m))]
            if seat_target is not None
        ]
        victim = None
        if kills:
            counts = Counter(kills)
            victim, _ = counts.most_common(1)[0]
            if protects and victim in protects:
                victim = None
        if victim is not None and victim in self.alive:
            self.remove_player(victim)
            self.death_reason[victim] = "night"
            self.history.append(f"Night {self.day}: {self._name(victim)} was eliminated.")
        await ctx.record_action("night_outcome", {
            "victim": victim,
            "history": list(self.history)
        })

        for seat, move in moves.items():
            target = self._parse_alive_target(self._normalize_target(move))
            if self.role[seat] == "detective" and target is not None:
                alignment = "mafia" if self.role.get(target) == "mafia" else "town"
                reveal = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Investigation Result",
                    status=f"{self._name(target)} is **{alignment.upper()}**",
                    status_emoji="enable_detective",
                )
                reveal.add_container(container)
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
            target = self._normalize_target(move)
            if target == "skip":
                continue
            seat_target = self._parse_alive_target(target)
            if seat_target is not None:
                tally[seat_target] += 1
        lynched = None
        if tally:
            top = tally.most_common()
            if len(top) == 1 or top[0][1] > top[1][1]:
                lynched = top[0][0]
                self.remove_player(lynched)
                self.death_reason[lynched] = "day"
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
        history: list[str] = []
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
            elif move.source == "forfeit":
                for p in self.players:
                    if p.seat == move.actor_seat:
                        takeover_info = {
                            "user_id": p.user_id,
                            "display_name": p.display_name,
                            "is_bot": p.is_bot,
                            "type": "removal",
                            "reason": move.arguments.get("reason", "forfeit"),
                        }
                if move.actor_seat is not None:
                    alive.discard(move.actor_seat)

            if move.source == "roles_assigned":
                roles = {int(k): v for k, v in move.arguments["roles"].items()}
                for p in self.players:
                    p.role_key = roles.get(p.seat)

                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Roles Assigned",
                    status="The game is about to begin.",
                    status_emoji="user",
                )
                forward = ctx.emoji.get("forward")
                role_lines = []
                for p in self.players:
                    role = roles.get(p.seat, "unknown")
                    role_emoji = self._role_emoji(ctx, role)
                    role_lines.append(
                        f"{ctx.emoji.get('bullet')} {_get_name(p.seat)} {forward} {role_emoji} **{role.title()}**"
                    )
                container.add_text(TextDisplay(markdown_content="\n".join(role_lines)))
                view.add_container(container)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Setup",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

            elif move.source == "night_start":
                day_num = move.arguments["day"]
                view = self._public_view(
                    ctx,
                    title=f"Night {day_num}",
                    status="Night falls across the town...",
                    status_emoji="timer",
                    alive=alive,
                    history=history,
                )
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label=f"Night {day_num}",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

            elif move.source in ("kill", "protect", "investigate"):
                actor_seat = move.actor_seat
                role = roles.get(actor_seat, "unknown") if actor_seat is not None else "unknown"
                target_seat = int(move.arguments.get("target")) if move.arguments.get("target") is not None else None
                target_str = _get_name(target_seat) if target_seat is not None else "no one"

                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title=f"Night Action: {role.title()}",
                    status=f"{_get_name(actor_seat) if actor_seat is not None else 'Unknown'} chose to **{move.source}** {target_str}",
                    status_emoji=self._ROLE_EMOJI.get(role, "user"),
                )
                view.add_container(container)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Night Action",
                        actor_seat=actor_seat,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

            elif move.source == "detective_reveal":
                detective_seat = move.arguments["detective"]
                target_seat = move.arguments["target"]
                alignment = move.arguments["alignment"]

                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Investigation Result",
                    status=f"{_get_name(detective_seat)} found {_get_name(target_seat)} is **{alignment.upper()}**",
                    status_emoji="enable_detective",
                )
                view.add_container(container)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Investigation",
                        actor_seat=detective_seat,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

            elif move.source == "night_outcome":
                victim = move.arguments.get("victim")
                history = move.arguments.get("history", [])
                if victim is not None:
                    alive.discard(int(victim))

                if victim is not None:
                    status = f"{_get_name(int(victim))} was eliminated during the night."
                else:
                    status = "No one was eliminated during the night."

                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Morning Results",
                    status=status,
                    status_emoji="success" if victim is None else "error",
                )
                container.add_separator()
                container.add_text(TextDisplay(markdown_content=self._alive_roster(ctx, alive)))
                view.add_container(container)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Morning",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
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
                if lynched is not None:
                    status = f"{_get_name(int(lynched))} was lynched by popular vote."
                    status_emoji = "error"
                else:
                    status = "The vote was skipped or tied. No one was lynched."
                    status_emoji = "hmm"
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Day Voting Results",
                    status=status,
                    status_emoji=status_emoji,
                )

                vote_lines = []
                for voter_str, target_str in votes_cast.items():
                    voter_seat = int(voter_str)
                    target_seat = int(target_str) if target_str != "skip" else None
                    target_display = _get_name(target_seat) if target_seat is not None else "Skip"
                    vote_lines.append(
                        f"{ctx.emoji.get('bullet')} {_get_name(voter_seat)} voted for **{target_display}**"
                    )
                if vote_lines:
                    container.add_text(TextDisplay(markdown_content="\n".join(vote_lines)))
                    container.add_separator()

                container.add_text(TextDisplay(markdown_content=self._alive_roster(ctx, alive)))
                view.add_container(container)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Lynch Vote",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

            elif move.source == "game_end":
                winning_faction = move.arguments.get("winning_faction", "unknown")
                view = self._game_over_view(ctx, winning_faction, roles)

                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Game Over",
                        actor_seat=None,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )

        return frames

    def _public_view(
        self,
        ctx: GameContext,
        *,
        title: str,
        status: str | None = None,
        status_emoji: str | None = None,
        alive: set[int] | None = None,
        history: list[str] | None = None,
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
        )
        container.add_text(TextDisplay(markdown_content=self._alive_roster(ctx, alive)))
        history_block = self._history_block(ctx, history)
        if history_block:
            container.add_separator()
            container.add_text(TextDisplay(markdown_content=history_block))
        view.add_container(container)
        return view

    def _day_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title=f"Day {self.day}",
            status="Discussion and vote — cast your ballot below.",
            status_emoji="loading",
        )
        container.add_text(TextDisplay(markdown_content=self._alive_roster(ctx)))
        history_block = self._history_block(ctx)
        if history_block:
            container.add_separator()
            container.add_text(TextDisplay(markdown_content=history_block))
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

        mafia_mentions = [str(p) for p in self.players if self.role[p.seat] == "mafia"]
        if winner == "mafia":
            description = f"Mafia won (parity reached) ({', '.join(mafia_mentions)})"
        else:
            description = "Town won (all Mafia eliminated)"

        player_descriptions = {}
        for player in self.players:
            role = self.role[player.seat]
            is_mafia = (role == "mafia")
            if winner == "mafia":
                if is_mafia:
                    desc = "Won (survived)" if player.seat in self.alive else "Lynched by Town" if self.death_reason.get(player.seat) == "day" else "Eliminated"
                else:
                    if player.seat in self.alive:
                        desc = "Let mafia reach parity"
                    elif self.death_reason.get(player.seat) == "day":
                        desc = "Lynched by Town"
                    elif self.death_reason.get(player.seat) == "night":
                        desc = "Killed by Mafia"
                    else:
                        desc = "Eliminated"
            else:
                if is_mafia:
                    desc = "Lynched by Town" if self.death_reason.get(player.seat) == "day" else "Eliminated"
                else:
                    if player.seat in self.alive:
                        desc = "Won (survived)"
                    elif self.death_reason.get(player.seat) == "day":
                        desc = "Lynched by Town"
                    elif self.death_reason.get(player.seat) == "night":
                        desc = "Killed by Mafia"
                    else:
                        desc = "Eliminated"
            player_descriptions[player.seat] = desc

        return GameOutcome(
            results=results,
            summary={
                "winning_faction": winner,
                "roles": role_map,
            },
            description=description,
            player_descriptions=player_descriptions,
        )

    def remove_player(self, seat: int) -> None:
        self.alive.discard(seat)

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        source, args = await asyncio.to_thread(choose_mafia_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=source, args=args)
