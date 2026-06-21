from __future__ import annotations

from strife.presentation.components import LayoutView, Container, Separator, TextDisplay, TextSize, ActionRow, Button, \
    ButtonStyle
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


def build_about_view(emoji: EmojiResolver, show_background: bool = False) -> LayoutView:
    view = LayoutView()
    brand = emoji.get("logo")
    container = Container()
    
    if not show_background:
        container.add_text(
            TextDisplay(
                markdown_content=f"### {brand} Strife",
                size_style=TextSize.HEADER
            )
        )
        container.add_separator(Separator(visible=False))

        container.add_text(
            TextDisplay(
                markdown_content=(
                    "Strife is an interactive, thread-based gaming platform built "
                    "for Discord. The goal of this project is to create a seamless gaming experience within Discord, "
                    "without having to resort to the limited Activities feature or third-party websites."
                )
            )
        )
        container.add_separator()
        container.add_text(
            TextDisplay(
                markdown_content="-# This project is developed by [@quantumbagel](https://github.com/quantumbagel)."
            )
        )

        nav = ActionRow()
        nav.add_button(
            Button(
                source="catalog",
                label="Browse Catalog",
                style=ButtonStyle.PRIMARY,
                emoji="game",
                route_prefix=P.CAT_NAV,
                payload={"page": 0},
            )
        )
        nav.add_button(
            Button(
                source="background",
                label="Background",
                style=ButtonStyle.SECONDARY,
                route_prefix=P.ABOUT_NAV,
                payload={"show_background": True},
            )
        )
        nav.add_button(
            Button(
                label="GitHub Repository",
                style=ButtonStyle.LINK,
                emoji="github",
                url="https://github.com/quantumbagel/Strife",
            )
        )
        container.add_action_row(nav)
    else:
        forward = emoji.get("forward")
        container.add_text(
            TextDisplay(
                markdown_content=f"### {brand} Strife {forward} Background",
                size_style=TextSize.HEADER
            )
        )
        container.add_separator(Separator(visible=False))
        container.add_text(
            TextDisplay(
                markdown_content=(
                    "Some background on how this project came about:\n"
                    "* Originally, this started when my friend made a Discord bot that could play Liar's Dice. ([LoRiggio](https://github.com/Pixelz22/LoRiggioDev))\n"
                    "* This gave me grand visions of a discord bot for arbitrary board games.\n"
                    "* However, I kinda suck at programming. Many of the core features (leaderboards, ELO, matchmaking, etc.) were half-baked.\n"
                    "* This ended up creating a lot of tech debt and a half-functional project. (see that here: [PlayCord](https://github.com/PlayCord/bot))\n"
                    "* After roughly a year of working on PlayCord on and off, I decided to lock in.\n"
                    "* The result of that is Strife, a much better implementation of the same concept.\n"
                    "\n"
                    "Total development time for this project: 2024-2026, over 750 hours of work.\n"
                    "If you like what I've done here, I'm always looking for new opportunities :D"
                )
            )
        )

        nav = ActionRow()
        nav.add_button(
            Button(
                source="back",
                label="Back",
                style=ButtonStyle.SECONDARY,
                emoji="previous",  # TODO: change this to a different back arrow?
                route_prefix=P.ABOUT_NAV,
                payload={"show_background": False},
            )
        )
        container.add_action_row(nav)

    view.add_container(container)
    return view
