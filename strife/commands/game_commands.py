from __future__ import annotations

import typing
import discord
from discord import app_commands
from strife.engine.metadata import ParamType, SlashMove
from strife.matchmaking.registries import SessionRegistries
from strife.engine.registry import GameRegistry


def create_slash_command(
    game_key: str,
    slash_move: SlashMove,
    sessions: SessionRegistries,
) -> app_commands.Command:
    params_parts = ["interaction: discord.Interaction"]
    for param in slash_move.params:
        if param.type == ParamType.INT:
            t_name = "int"
        elif param.choices:
            choices_str = ", ".join(repr(c) for c in param.choices)
            t_name = f"typing.Literal[{choices_str}]"
        else:
            t_name = "str"

        default = ""
        if not param.required:
            default = " = None"
        params_parts.append(f"{param.name}: {t_name}{default}")

    params_str = ", ".join(params_parts)
    func_name = f"dynamic_cmd_{game_key}_{slash_move.name}"

    exec_globals = {
        **globals(),
        "discord": discord,
        "sessions": sessions,
        "typing": typing,
    }

    code_lines = [
        f"async def {func_name}({params_str}):",
        "    session = sessions.get_game(interaction.channel.id)",
        "    if session is None:",
        "        await interaction.response.send_message('No active game in this channel.', ephemeral=True)",
        "        return",
        "    args = {" + ", ".join(f"'{p.name}': {p.name}" for p in slash_move.params) + "}",
        f"    source = '{slash_move.name}'",
        "    try:",
        "        await interaction.response.defer(ephemeral=True)",
        "        await session.handle_slash_command(interaction.user.id, source, args)",
        "        await interaction.followup.send('Move submitted!', ephemeral=True)",
        "    except Exception as e:",
        "        msg = str(e)",
        "        if msg == 'cannot_act':",
        "            await interaction.followup.send('It is not your turn or you cannot act right now.', ephemeral=True)",
        "        elif msg == 'invalid_action':",
        "            await interaction.followup.send('Invalid action for this turn.', ephemeral=True)",
        "        else:",
        "            await interaction.followup.send(f'Error submitting move: {msg}', ephemeral=True)",
    ]

    code_str = "\n".join(code_lines)
    exec(code_str, exec_globals)
    callback = exec_globals[func_name]

    cmd = discord.app_commands.Command(
        name=slash_move.name,
        description=slash_move.description,
        callback=callback,
    )

    descriptions = {}
    for param in slash_move.params:
        if param.description:
            descriptions[param.name] = param.description
    cmd._params_description = descriptions

    for param in slash_move.params:
        if param.autocomplete:
            async def autocomplete_wrapper(interaction: discord.Interaction, current: str, p=param):
                try:
                    choices = await p.autocomplete(interaction, current)
                    return [discord.app_commands.Choice(name=c, value=c) for c in choices][:25]
                except Exception:
                    return []

            cmd.autocomplete(param.name)(autocomplete_wrapper)

    return cmd


def register_game_slash_commands(
    tree: app_commands.CommandTree,
    registry: GameRegistry,
    sessions: SessionRegistries,
) -> None:
    for meta in registry.all():
        if meta.slash_moves:
            group = app_commands.Group(name=meta.key, description=f"{meta.name} gameplay commands")
            for slash_move in meta.slash_moves:
                cmd = create_slash_command(meta.key, slash_move, sessions)
                group.add_command(cmd)
            tree.add_command(group)
