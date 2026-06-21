from __future__ import annotations

from strife.config.text import TextConfig
from strife.presentation.components import LayoutView, Container, Separator, TextDisplay, TextSize, ActionRow, Button, \
    ButtonStyle
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


def build_about_view(emoji: EmojiResolver, text: TextConfig, show_background: bool = False) -> LayoutView:
    view = LayoutView()
    brand = emoji.get("logo")
    container = Container()
    
    if not show_background:
        container.add_text(
            TextDisplay(
                markdown_content=text.get("about.title", logo=brand),
                size_style=TextSize.HEADER
            )
        )
        container.add_separator(Separator(visible=False))

        container.add_text(
            TextDisplay(
                markdown_content=text.get("about.description")
            )
        )
        container.add_separator()
        container.add_text(
            TextDisplay(
                markdown_content=text.get("about.developer")
            )
        )

        nav = ActionRow()
        nav.add_button(
            Button(
                source="catalog",
                label=text.get("about.browse_catalog_btn"),
                style=ButtonStyle.PRIMARY,
                emoji="game",
                route_prefix=P.CAT_NAV,
                payload={"page": 0},
            )
        )
        nav.add_button(
            Button(
                source="background",
                label=text.get("about.background_btn"),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.ABOUT_NAV,
                payload={"show_background": True},
            )
        )
        nav.add_button(
            Button(
                label=text.get("about.github_btn"),
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
                markdown_content=text.get("about.bg_title", logo=brand, forward=forward),
                size_style=TextSize.HEADER
            )
        )
        container.add_separator(Separator(visible=False))
        container.add_text(
            TextDisplay(
                markdown_content=text.get("about.bg_description")
            )
        )
        container.add_separator(Separator(visible=False))

        container.add_text(
            TextDisplay(
                markdown_content="-# bagel ❤️ OSS: All my projects are open source"
            )
        )


        nav = ActionRow()
        nav.add_button(
            Button(
                source="back",
                label=text.get("about.back_btn"),
                style=ButtonStyle.SECONDARY,
                emoji="previous",  # TODO: change this to a different back arrow?
                route_prefix=P.ABOUT_NAV,
                payload={"show_background": False},
            )
        )
        container.add_action_row(nav)

    view.add_container(container)
    return view

