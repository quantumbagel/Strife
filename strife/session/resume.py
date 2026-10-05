from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import discord

from strife.engine.log import LogEntryKind
from strife.engine.log_cursor import log_ends_match
from strife.engine.players import Move, Player
from strife.engine.requests import TimeoutConsequence
from strife.logging import get_logger
from strife.persistence.repositories import FinishedMatch, MatchDetail
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P
from strife.session.header import build_game_thread_header_view
from strife.session import GameSession

log = get_logger("session.resume")


def _players_from_match(detail: MatchDetail) -> list[Player]:
    return [
        Player(
            seat=p.seat_index,
            user_id=p.user_id,
            display_name=p.display_name,
            is_bot=False if p.user_id is not None else p.is_bot,
            bot_difficulty=None if p.user_id is not None else p.bot_difficulty,
            role_key=p.role_key,
        )
        for p in detail.players
    ]


def _apply_host_bookkeeping(
    players: list[Player], moves: list[Move]
) -> tuple[set[int], set[int], dict[int, int]]:
    taken_over: set[int] = set()
    removed: set[int] = set()
    timeout_strikes: dict[int, int] = {}
    by_seat = {p.seat: p for p in players}
    for move in moves:
        if not move.is_system:
            continue
        if move.source == "bot_takeover":
            seat = move.args.get("seat")
            if seat is None:
                continue
            seat = int(seat)
            taken_over.add(seat)
            player = by_seat.get(seat)
            if player is not None:
                player.is_bot = True
                diff = move.args.get("bot_difficulty")
                if diff is not None:
                    player.bot_difficulty = str(diff)
        elif move.source == "forfeit" and move.args.get("removed"):
            seat = move.actor_seat
            if seat is not None:
                removed.add(int(seat))
        elif move.source == "timeout_strike":
            seat = move.args.get("seat")
            count = move.args.get("count")
            if seat is None or count is None:
                continue
            timeout_strikes[int(seat)] = int(count)
    return taken_over, removed, timeout_strikes


async def _fetch_thread(bot, thread_id: int) -> discord.Thread | None:
    try:
        channel = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
    except discord.NotFound, discord.Forbidden:
        return None
    except Exception:
        log.exception("Failed to fetch live match thread %s", thread_id)
        return None
    return channel if isinstance(channel, discord.Thread) else None


async def _fetch_message(channel, message_id: int | None) -> discord.Message | None:
    if channel is None or message_id is None:
        return None
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound, discord.Forbidden:
        return None
    except Exception:
        log.exception(
            "Failed to fetch message %s in %s", message_id, getattr(channel, "id", "?")
        )
        return None


def _timeout_consequence(raw: str | None) -> TimeoutConsequence:
    if not raw:
        return TimeoutConsequence.ABANDON
    try:
        return TimeoutConsequence(raw)
    except ValueError:
        return TimeoutConsequence.ABANDON


async def abandon_unresumed(
    bot, matches, live: MatchDetail, moves: list[Move], *, thread=None
) -> None:
    notice = bot.config.text.get("match.interrupted")
    if isinstance(thread, discord.Thread):
        try:
            await thread.send(notice)
        except discord.HTTPException as exc:
            log.warning("Couldn't post interrupt notice in %s: %s", live.thread_id, exc)
        try:
            await thread.edit(locked=True, archived=True)
        except discord.HTTPException as exc:
            log.warning("Couldn't lock interrupted thread %s: %s", live.thread_id, exc)
    next_index = max((m.turn_index for m in moves), default=-1) + 1
    game_end = Move(
        actor_seat=None,
        source="game_end",
        args={"reason": "interrupted", "cancelled": True},
        kind=LogEntryKind.SYSTEM,
        turn_index=next_index,
        created_at=datetime.now(timezone.utc),
    )
    finished = FinishedMatch(
        code=live.code,
        game_key=live.game_key,
        guild_id=live.guild_id,
        thread_id=live.thread_id,
        seed=live.seed,
        settings=live.settings,
        status="abandoned",
        outcome={
            "summary": {"reason": "interrupted"},
            "description": notice,
            "player_descriptions": {},
        },
        total_turns=sum(1 for m in moves if m.is_game and m.actor_seat is not None),
        players=list(live.players),
        moves=[*moves, game_end],
        started_at=live.started_at,
        ended_at=datetime.now(timezone.utc),
        match_id=live.id,
        game_version=live.game_version,
        board_message_id=live.board_message_id,
        header_message_id=live.header_message_id,
        lobby_channel_id=live.lobby_channel_id,
        lobby_message_id=live.lobby_message_id,
        turn_timeout_seconds=live.turn_timeout_seconds,
        turn_timeout_max_strikes=live.turn_timeout_max_strikes,
        turn_timeout_consequence=live.turn_timeout_consequence,
        lobby_private=live.lobby_private,
        lobby_creator_id=live.lobby_creator_id,
    )
    try:
        await matches.finish(finished)
    except Exception:
        log.exception("Failed to mark live match %s abandoned", live.id)


async def resume_live_matches(bot) -> None:
    matches = getattr(bot, "matches", None)
    moves_repo = getattr(bot, "moves", None)
    registry = getattr(bot, "game_registry", None)
    if matches is None or moves_repo is None or registry is None:
        return
    if getattr(bot, "lobby", None) is None or getattr(bot, "sessions", None) is None:
        return
    try:
        live_rows = await matches.list_live()
    except Exception:
        log.exception("Failed to list live matches for resume")
        return
    if not live_rows:
        return
    for live in live_rows:
        await _resume_one(bot, live, matches, moves_repo, registry)
    log.info("Finished resume pass for %s live match(es)", len(live_rows))


async def _resume_one(bot, live: MatchDetail, matches, moves_repo, registry) -> None:
    thread_id = live.thread_id
    try:
        stored_moves = await moves_repo.list_for_match(live.id)
    except Exception:
        log.exception("Failed to load moves for live match %s", live.id)
        await abandon_unresumed(bot, matches, live, [])
        return

    skip_reason = None
    if thread_id is None:
        skip_reason = "no thread"
    elif not registry.contains(live.game_key):
        skip_reason = "game not registered"
    else:
        if live.game_build is not None:
            plugin_manager = getattr(bot, "plugin_manager", None)
            current_build = (
                plugin_manager.build_for(live.game_key)
                if plugin_manager is not None
                else None
            )
            if current_build != live.game_build:
                skip_reason = "plugin build changed"
        else:
            current = registry.metadata(live.game_key).version
            if live.game_version is None or live.game_version != current:
                skip_reason = "plugin version changed"
        if skip_reason is None and log_ends_match(stored_moves):
            skip_reason = "log already ended"

    thread = await _fetch_thread(bot, thread_id) if thread_id is not None else None
    board_message = None
    header_message = None
    if skip_reason is None:
        if thread is None:
            skip_reason = "thread missing"
        else:
            if thread.archived or thread.locked:
                try:
                    await thread.edit(archived=False, locked=False)
                except discord.Forbidden:
                    log.warning(
                        "Couldn't unarchive/unlock thread %s for live match %s",
                        thread.id,
                        live.id,
                    )
                except discord.HTTPException as exc:
                    log.warning(
                        "Couldn't unarchive/unlock thread %s for live match %s: %s",
                        thread.id,
                        live.id,
                        exc,
                    )
            board_message = await _fetch_message(thread, live.board_message_id)
            header_message = await _fetch_message(thread, live.header_message_id)

    if skip_reason is not None:
        log.info(
            "Skipping resume of match %s (%s): %s", live.id, live.game_key, skip_reason
        )
        await abandon_unresumed(bot, matches, live, stored_moves, thread=thread)
        return

    assert thread is not None

    game_players = _players_from_match(live)
    session_players = [dataclasses.replace(p) for p in game_players]
    taken_over, removed, timeout_strikes = _apply_host_bookkeeping(
        session_players, stored_moves
    )
    settings = dict(live.settings or {})
    session_settings = dict(settings)
    session_settings.pop("creator_id", None)
    try:
        game = registry.create(live.game_key, game_players, settings, live.seed)
    except Exception:
        log.exception("Failed to reconstruct game for live match %s", live.id)
        await abandon_unresumed(bot, matches, live, stored_moves, thread=thread)
        return

    compiler = bot.lobby.compiler.for_game(live.game_key)
    header_surface = ViewSurface(compiler, prefix=P.REPLAY_NOOP, resource_id=thread.id)
    if header_message is not None:
        header_surface.bind(header_message)
    else:
        starting_view = build_game_thread_header_view(
            players=session_players,
            text=bot.config.text,
            emoji=bot.lobby.emoji,
            owner_ids=bot.lobby.owner_ids(),
        )
        try:
            await header_surface.send_to_thread(thread, starting_view)
        except Exception:
            log.exception(
                "Failed to recreate header message for live match %s", live.id
            )
        if header_surface.message_id is not None:
            try:
                await bot.lobby.finalizer.set_header_message(
                    live.id, header_surface.message_id
                )
            except Exception:
                log.exception(
                    "Failed to persist header message for live match %s", live.id
                )
    game_surface = ViewSurface(compiler, prefix=P.G_MOVE, resource_id=thread.id)
    if board_message is not None:
        game_surface.bind(board_message)

    lobby_surface = None
    if live.lobby_channel_id and live.lobby_message_id:
        try:
            channel = bot.get_channel(live.lobby_channel_id) or await bot.fetch_channel(
                live.lobby_channel_id
            )
        except Exception:
            channel = None
        lobby_message = await _fetch_message(channel, live.lobby_message_id)
        if lobby_message is not None:
            lobby_surface = ViewSurface(
                bot.lobby.compiler, prefix=P.LOBBY_JOIN, resource_id=thread.id
            )
            lobby_surface.bind(lobby_message)

    session = GameSession(
        thread_id=thread.id,
        guild_id=live.guild_id,
        game=game,
        players=session_players,
        settings=session_settings,
        seed=live.seed,
        surface=game_surface,
        text=bot.config.text,
        finalizer=bot.lobby.finalizer,
        game_key=live.game_key,
        header_surface=header_surface,
        turn_timeout_seconds=live.turn_timeout_seconds or 90,
        turn_timeout_max_strikes=live.turn_timeout_max_strikes or 3,
        turn_timeout_consequence=_timeout_consequence(live.turn_timeout_consequence),
    )
    session.log.preload(stored_moves)
    session._match_id = live.id
    session._match_code = live.code
    if live.started_at is not None:
        session._started_at = live.started_at
    session.game_version = live.game_version
    session.lobby_surface = lobby_surface
    session.lobby_private = live.lobby_private
    session.lobby_creator_id = live.lobby_creator_id
    session.lobby_channel_id = live.lobby_channel_id
    session.lobby_message_id = live.lobby_message_id
    session.taken_over = taken_over
    session._removed_seats = removed
    session.timeout_strikes = timeout_strikes
    session.set_bot(bot)
    try:
        await bot.sessions.register_session(session)
        await session.start(resume=True)
    except Exception:
        log.exception("Failed to start resumed session for match %s", live.id)
        try:
            await bot.sessions.drop_game(session.thread_id)
            for player in session.players:
                if player.user_id:
                    await bot.sessions.release_user(
                        player.user_id, thread_id=session.thread_id
                    )
        except Exception:
            log.exception(
                "Failed to release occupancy after resume failure %s", live.id
            )
        await abandon_unresumed(bot, matches, live, stored_moves, thread=thread)
