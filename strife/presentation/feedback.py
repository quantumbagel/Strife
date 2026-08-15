from __future__ import annotations

from dataclasses import dataclass

import discord

from strife.config.text import TextConfig
from strife.presentation.compiler import Compiler
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
)
from strife.presentation.style import add_body, add_divider, add_header


@dataclass
class FeedbackAction:
    label_key: str
    style: ButtonStyle
    emoji: str | None = None
    link_url: str | None = None
    route_prefix: str | None = None
    resource_id: int | None = None
    source: str = ""
    payload: dict | None = None


def build_feedback_container(
    *,
    icon: str,
    title: str,
    body: str | None = None,
    body_heading: str | None = None,
) -> Container:
    container = Container()
    add_header(container, title, emoji=icon)
    if body:
        add_divider(container)
        content = f"**{body_heading}** {body}" if body_heading else body
        add_body(container, content)
    return container


def add_feedback_actions(
    container: Container,
    actions: list[FeedbackAction],
    *,
    text: TextConfig,
) -> None:
    if not actions:
        return
    row = ActionRow()
    for action in actions:
        if action.link_url:
            row.add_button(
                Button(
                    label=text.get(action.label_key),
                    style=ButtonStyle.LINK,
                    emoji=action.emoji,
                    url=action.link_url,
                )
            )
        else:
            row.add_button(
                Button(
                    source=action.source,
                    label=text.get(action.label_key),
                    style=action.style,
                    emoji=action.emoji,
                    route_prefix=action.route_prefix,
                    resource_id=action.resource_id,
                    payload=action.payload,
                )
            )
    container.add_action_row(row)


def build_feedback_view(
    *,
    icon: str,
    title: str,
    body: str | None = None,
    body_heading: str | None = None,
    actions: list[FeedbackAction] | None = None,
    text: TextConfig,
) -> LayoutView:
    container = build_feedback_container(
        icon=icon,
        title=title,
        body=body,
        body_heading=body_heading,
    )
    if actions:
        add_feedback_actions(container, actions, text=text)
    view = LayoutView()
    view.add_container(container)
    return view


async def send_ephemeral_feedback(
    interaction: discord.Interaction,
    view: LayoutView,
    *,
    compiler: Compiler,
    prefix: str,
    resource_id: int,
) -> None:
    compiled = compiler.compile(view, resource_id=resource_id, prefix=prefix)
    if not interaction.response.is_done():
        await interaction.response.send_message(view=compiled, ephemeral=True)
    else:
        await interaction.followup.send(view=compiled, ephemeral=True)


async def disable_feedback_actions(interaction: discord.Interaction) -> None:
    if not interaction.message:
        return
    try:
        view = discord.ui.LayoutView.from_message(interaction.message)
        for item in view.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        if not interaction.response.is_done():
            await interaction.response.edit_message(view=view)
        else:
            await interaction.message.edit(view=view)
    except Exception:
        pass
