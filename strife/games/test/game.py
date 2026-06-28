from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from typing import Any

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import MoveRecord
from strife.presentation.compiler import clone_and_disable
from strife.presentation.components import (
    ActionRow,
    Align,
    Button,
    ButtonStyle,
    ChannelSelect,
    Container,
    LayoutView,
    MediaGallery,
    MediaGalleryItem,
    Section,
    Select,
    SelectChoice,
    Separator,
    TextDisplay,
    TextSize,
)


class TestGame(Game):
    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng: random.Random):
        super().__init__(players, settings, rng)
        self.alive = set(p.seat for p in players)
        self.phase = 1
        self.last_interacted_source: str | None = None
        self.last_interacted_args: dict | None = None
        self.phase2_votes: dict[int, str] = {}
        self.phase3_confirmed: set[int] = set()
        self.secrets: dict[int, str] = {}
        self.removed_players: set[int] = set()

    def remove_player(self, seat: int) -> None:
        self.alive.discard(seat)
        self.removed_players.add(seat)

    def _forfeit_outcome(self, forfeiter_seat: int) -> GameOutcome:
        results: dict[int, str] = {}
        player_descriptions: dict[int, str] = {}
        for player in self.players:
            if player.seat == forfeiter_seat:
                results[player.seat] = "loss"
                player_descriptions[player.seat] = "Forfeited"
            elif player.seat in self.alive:
                results[player.seat] = "win"
                player_descriptions[player.seat] = "Opponent forfeited"
            else:
                results[player.seat] = "loss"
                player_descriptions[player.seat] = "Removed from play"
        forfeiter_name = self.players[forfeiter_seat].display_name
        return GameOutcome(
            results=results,
            summary={"reason": "forfeit", "forfeiter": forfeiter_seat},
            description=f"{forfeiter_name} forfeited",
            player_descriptions=player_descriptions,
        )

    def _should_end_after_forfeit(self) -> bool:
        if not self.alive:
            return True
        alive_humans = [
            player for player in self.players if player.seat in self.alive and not player.is_bot
        ]
        if not alive_humans:
            return True
        return len(self.alive) < self.metadata.player_count.min_players

    def validate_roles(self, assignment: dict[int, str]) -> tuple[bool, str | None]:
        for role in assignment.values():
            if role not in {"tester", "observer"}:
                return False, f"Unknown role assignment: {role}"
        return True, None

    def _phase1_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        view.header("first", "API Test Phase 1: Layout Components Showcase", emoji_resolver=ctx.emoji)

        container = Container()
        container.add_text(
            TextDisplay(
                "Subheader Alignment Check",
                size_style=TextSize.SUBHEADER,
                alignment=Align.CENTER,
            )
        )
        container.add_text(
            TextDisplay(
                "This body text demonstrates standard messaging. Below is a visible separator."
            )
        )
        container.add_separator(Separator(visible=True))

        # Test Section with accessory button
        sec = Section(
            accessory=Button(source="btn_sect", label="Section Accessory", style=ButtonStyle.PRIMARY)
        )
        sec.add_text(TextDisplay("This text is inside a Section layout element."))
        container.add_section(sec)

        # Test Separator (invisible)
        container.add_separator(Separator(visible=False))

        # Test Buttons in ActionRow
        btn_row = ActionRow()
        btn_row.add_button(Button(source="btn_primary", label="Primary", style=ButtonStyle.PRIMARY))
        btn_row.add_button(
            Button(source="btn_secondary", label="Secondary", style=ButtonStyle.SECONDARY)
        )
        btn_row.add_button(
            Button(source="btn_success", label="Success", style=ButtonStyle.SUCCESS, emoji="ready")
        )
        btn_row.add_button(Button(source="btn_danger", label="Danger", style=ButtonStyle.DANGER))
        btn_row.add_button(
            Button(label="External Link", style=ButtonStyle.LINK, url="https://github.com/quantumbagel/Strife")
        )
        container.add_action_row(btn_row)

        # Test Select component
        sel_row = ActionRow()
        sel_row.add_select(
            Select(
                source="sel_choices",
                placeholder="Choose options (min 1, max 2)...",
                min_values=1,
                max_values=2,
                choices=[
                    SelectChoice(
                        label="Choice 1",
                        value="choice_1",
                        description="Description 1",
                        emoji="hmm",
                    ),
                    SelectChoice(
                        label="Choice 2",
                        value="choice_2",
                        description="Description 2",
                        default=True,
                    ),
                    SelectChoice(label="Choice 3", value="choice_3"),
                ],
            )
        )
        container.add_action_row(sel_row)

        # Test ChannelSelect component
        chan_row = ActionRow()
        chan_row.add_channel_select(
            ChannelSelect(source="sel_channel", placeholder="Pick a text channel", channel_types=("text",))
        )
        container.add_action_row(chan_row)

        # Test MediaGallery component
        gallery = MediaGallery()
        gallery.add_item(
            MediaGalleryItem(
                media_url="https://raw.githubusercontent.com/discordjs/discord.js/main/assets/logo.png",
                description="Discord JS Library Logo",
            )
        )
        container.set_gallery(gallery)

        # Test action to proceed
        next_row = ActionRow()
        next_row.add_button(Button(source="btn_next_1", label="Go to Phase 2 ➡️", style=ButtonStyle.SUCCESS))
        container.add_action_row(next_row)

        # Interaction Feedback display
        if self.last_interacted_source is not None:
            container.add_separator(Separator(visible=True))
            feedback = f"Last Interaction: **{self.last_interacted_source}**"
            if self.last_interacted_args:
                feedback += f" with parameters: `{self.last_interacted_args}`"
            container.add_text(TextDisplay(feedback))

        view.add_container(container)
        return view

    def _phase2_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        view.header("hmm", "API Test Phase 2: Simultaneous Inputs", emoji_resolver=ctx.emoji)

        container = Container()
        container.add_text(
            TextDisplay(
                "This phase collects votes/input from ALL active players simultaneously.",
                size_style=TextSize.BODY,
            )
        )

        for p in self.players:
            if p.seat in self.alive:
                vote_status = self.phase2_votes.get(p.seat, "Pending...")
                container.add_text(TextDisplay(f"• {p.display_name}: **{vote_status}**"))

        row = ActionRow()
        row.add_select(
            Select(
                source="vote_input",
                placeholder="Cast your vote!",
                choices=[
                    SelectChoice(label="Agree", value="agree"),
                    SelectChoice(label="Disagree", value="disagree"),
                    SelectChoice(label="Abstain", value="abstain"),
                ],
            )
        )
        container.add_action_row(row)

        view.add_container(container)
        return view

    def _phase3_public_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        view.header("ready", "API Test Phase 3: Private Messages", emoji_resolver=ctx.emoji)

        container = Container()
        container.add_text(
            TextDisplay(
                "A secret private message has been sent to each active player.",
                size_style=TextSize.BODY,
            )
        )

        for p in self.players:
            if p.seat in self.alive:
                status = "Confirmed ✅" if p.seat in self.phase3_confirmed else "Waiting ⏳"
                container.add_text(TextDisplay(f"• {p.display_name}: **{status}**"))

        row = ActionRow()
        row.add_button(Button(source="btn_confirm_secret", label="I read my secret!", style=ButtonStyle.SUCCESS))
        container.add_action_row(row)

        view.add_container(container)
        return view

    def _phase4_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        view.header("user", "API Test Phase 4: Engine Context Details", emoji_resolver=ctx.emoji)

        container = Container()
        container.add_text(
            TextDisplay(
                "Here are the metadata and runtime parameters retrieved from `GameContext`:",
                size_style=TextSize.SUBHEADER,
            )
        )

        started_str = ctx.started_at.isoformat() if ctx.started_at else "None"
        container.add_text(TextDisplay(f"• **Started At:** {started_str}"))
        container.add_text(TextDisplay(f"• **Is Replay:** {ctx.is_replay}"))

        container.add_separator()
        container.add_text(TextDisplay("### Game Settings", size_style=TextSize.SUBHEADER))
        for k, v in ctx.settings.items():
            container.add_text(TextDisplay(f"• `{k}`: `{v}`"))

        container.add_separator()
        container.add_text(TextDisplay("### Players List", size_style=TextSize.SUBHEADER))
        for p in self.players:
            is_bot_status = ctx.is_bot(p.seat)
            alive_status = "Alive" if p.seat in self.alive else "Removed"
            role_assigned = p.role_key or "None"
            container.add_text(
                TextDisplay(
                    f"• Seat {p.seat}: {p.display_name} (Bot: {is_bot_status}, Status: {alive_status}, Role: {role_assigned})"
                )
            )

        row = ActionRow()
        row.add_button(Button(source="btn_finish", label="Finish Game 🏁", style=ButtonStyle.PRIMARY))
        container.add_action_row(row)

        view.add_container(container)
        return view

    async def play(self, ctx: GameContext) -> GameOutcome:
        # Phase 1: Layout Components & Interaction
        self.phase = 1
        while True:
            if not self.alive:
                return self._forfeit_outcome(next(iter(self.removed_players)))
            view = self._phase1_view(ctx)
            actor = sorted(self.alive)[0]
            move = await ctx.request_input(
                view,
                actor=actor,
                sources={
                    "btn_sect",
                    "btn_primary",
                    "btn_secondary",
                    "btn_success",
                    "btn_danger",
                    "sel_choices",
                    "sel_channel",
                    "btn_next_1",
                },
            )
            await ctx.record_action(move.source, move.args)
            if move.source == "forfeit" and self._should_end_after_forfeit():
                return self._forfeit_outcome(move.actor_seat)
            if move.source == "btn_next_1":
                break

            self.last_interacted_source = move.source
            self.last_interacted_args = move.args

        # Phase 2: Simultaneous inputs
        self.phase = 2
        view = self._phase2_view(ctx)
        await ctx.update(view)

        actors = set(self.alive)
        moves = await ctx.request_inputs(view, actors=actors, sources={"vote_input"}, until="all")
        for seat, move in moves.items():
            val = move.args.get("value") or (
                move.args.get("values")[0] if move.args.get("values") else "unknown"
            )
            self.phase2_votes[seat] = val
            await ctx.record_action("cast_vote", {"seat": seat, "vote": val})

        await ctx.update(self._phase2_view(ctx))

        # Phase 3: Private message sending
        self.phase = 3
        words = ["antigravity", "bagel", "quantum", "strife", "victory", "secret", "discord"]
        for p in self.players:
            if p.seat in self.alive:
                secret = self.rng.choice(words)
                self.secrets[p.seat] = secret

                priv_view = LayoutView()
                priv_view.header("user", "Top Secret Information", emoji_resolver=ctx.emoji)
                priv_container = Container()
                priv_container.add_text(
                    TextDisplay(f"Hello **{p.display_name}**! Your secret code word is:")
                )
                priv_container.add_text(TextDisplay(f"## {secret}", size_style=TextSize.HEADER))
                priv_container.add_text(
                    TextDisplay(
                        "Please return to the game thread and confirm you read it.",
                        size_style=TextSize.BODY,
                    )
                )
                priv_view.add_container(priv_container)

                await ctx.send_private(p.seat, priv_view)
                await ctx.record_action("send_private_secret", {"seat": p.seat, "secret": secret})

        while len(self.phase3_confirmed) < len(self.alive):
            pub_view = self._phase3_public_view(ctx)
            pending_actors = set(self.alive) - self.phase3_confirmed
            moves = await ctx.request_inputs(
                pub_view, actors=pending_actors, sources={"btn_confirm_secret"}, until="any"
            )
            for seat, move in moves.items():
                if move.source == "btn_confirm_secret":
                    self.phase3_confirmed.add(seat)
                    await ctx.record_action("confirm_secret", {"seat": seat})

        # Phase 4: Settings & Properties Check
        self.phase = 4
        view = self._phase4_view(ctx)
        if not self.alive:
            return self._forfeit_outcome(next(iter(self.removed_players)))
        actor = sorted(self.alive)[0]
        move = await ctx.request_input(view, actor=actor, sources={"btn_finish"})
        await ctx.record_action(move.source, move.args)

        results = {}
        player_descriptions = {}
        for p in self.players:
            if p.seat in self.alive:
                results[p.seat] = "win"
                player_descriptions[p.seat] = "API verification complete!"
            else:
                results[p.seat] = "loss"
                player_descriptions[p.seat] = "Removed from play."

        return GameOutcome(
            results=results,
            summary={
                "verified": True,
                "phase2_votes": self.phase2_votes,
                "secrets": self.secrets,
                "removed_players": list(self.removed_players),
            },
            description="API Test completed successfully.",
            player_descriptions=player_descriptions,
        )

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        view = LayoutView()
        view.header("ready", "API Test: Final Summary", emoji_resolver=ctx.emoji)

        container = Container()
        container.add_text(
            TextDisplay("The API Test game has concluded successfully.", size_style=TextSize.SUBHEADER)
        )
        container.add_text(TextDisplay(f"**Outcome Description:** {outcome.description}"))

        summary = outcome.summary or {}
        container.add_separator()
        container.add_text(TextDisplay("### Summary Details", size_style=TextSize.SUBHEADER))
        container.add_text(TextDisplay(f"• **Verified:** {summary.get('verified')}"))
        container.add_text(TextDisplay(f"• **Phase 2 Votes:** `{summary.get('phase2_votes')}`"))
        container.add_text(TextDisplay(f"• **Generated Secrets:** `{summary.get('secrets')}`"))
        container.add_text(TextDisplay(f"• **Removed Players:** `{summary.get('removed_players')}`"))

        view.add_container(container)
        return view

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.alive = set(p.seat for p in self.players)
        self.phase = 1
        self.last_interacted_source = None
        self.last_interacted_args = None
        self.phase2_votes = {}
        self.phase3_confirmed = set()
        self.secrets = {}
        self.removed_players = set()

        frames: list[ReplayFrame] = []

        initial_view = self._phase1_view(ctx)
        frames.append(
            ReplayFrame(
                index=len(frames),
                turn_label="Game Start",
                actor_seat=None,
                view=clone_and_disable(initial_view),
                timestamp=ctx.started_at,
            )
        )

        for move in moves:
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
            elif move.source in ("forfeit", "game_end"):
                if move.source == "forfeit":
                    for p in self.players:
                        if p.seat == move.actor_seat:
                            takeover_info = {
                                "display_name": p.display_name,
                                "type": "removal",
                                "reason": move.arguments.get("reason", "forfeit"),
                            }

            # Replicate state transitions based on move source
            if move.source == "btn_next_1":
                self.phase = 2
            elif move.source == "cast_vote":
                seat = move.arguments.get("seat")
                vote = move.arguments.get("vote")
                if seat is not None:
                    self.phase2_votes[seat] = vote
            elif move.source == "send_private_secret":
                seat = move.arguments.get("seat")
                secret = move.arguments.get("secret")
                if seat is not None:
                    self.secrets[seat] = secret
                self.phase = 3
            elif move.source == "confirm_secret":
                seat = move.arguments.get("seat")
                if seat is not None:
                    self.phase3_confirmed.add(seat)
            elif move.source == "btn_finish":
                self.phase = 4
            else:
                if self.phase == 1:
                    self.last_interacted_source = move.source
                    self.last_interacted_args = move.arguments

            if self.phase == 1:
                view = self._phase1_view(ctx)
            elif self.phase == 2:
                view = self._phase2_view(ctx)
            elif self.phase == 3:
                view = self._phase3_public_view(ctx)
            else:
                view = self._phase4_view(ctx)

            frames.append(
                ReplayFrame(
                    index=len(frames),
                    turn_label=f"Turn {move.turn_index + 1}",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                )
            )

        return frames

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        if self.phase == 1:
            if self.rng.random() < 0.5:
                return Move(actor_seat=seat, source="btn_next_1", args={})
            return Move(actor_seat=seat, source="btn_primary", args={})
        elif self.phase == 2:
            val = self.rng.choice(["agree", "disagree", "abstain"])
            return Move(actor_seat=seat, source="vote_input", args={"value": val, "values": [val]})
        elif self.phase == 3:
            return Move(actor_seat=seat, source="btn_confirm_secret", args={})
        elif self.phase == 4:
            return Move(actor_seat=seat, source="btn_finish", args={})
        return Move(actor_seat=seat, source="btn_finish", args={})
