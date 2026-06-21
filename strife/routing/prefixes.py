from __future__ import annotations

G_MOVE = "g_move:"
G_SELECT = "g_select:"
R_NAV = "r_nav:"
REMATCH = "rematch:"
REPLAY_NOOP = "replay_noop:"
CAT_NAV = "cat_nav:"
PROF_NAV = "prof_nav:"
PROF_OPEN = "prof_open:"

LOBBY_JOIN = "lobby_join:"
LOBBY_LEAVE = "lobby_leave:"
LOBBY_READY = "lobby_ready:"
LOBBY_ASSIGN = "lobby_assign:"
LOBBY_SETTINGS = "lobby_settings:"
LOBBY_ROLE = "lobby_role:"
LOBBY_PRIV = "lobby_priv:"
LOBBY_RESET_PRIV = "lobby_reset_priv:"
LOBBY_OPT = "lobby_opt:"
LOBBY_RESET_RULES = "lobby_reset_rules:"
LOBBY_END = "lobby_end:"

ABOUT_NAV = "about_nav:"

GAME_PREFIXES = {G_MOVE, G_SELECT}
LOBBY_PREFIXES = {
    LOBBY_JOIN,
    LOBBY_LEAVE,
    LOBBY_READY,
    LOBBY_ASSIGN,
    LOBBY_SETTINGS,
    LOBBY_ROLE,
    LOBBY_PRIV,
    LOBBY_RESET_PRIV,
    LOBBY_OPT,
    LOBBY_RESET_RULES,
    LOBBY_END,
}

ALL_PREFIXES = GAME_PREFIXES | LOBBY_PREFIXES | {
    R_NAV,
    REMATCH,
    REPLAY_NOOP,
    CAT_NAV,
    PROF_NAV,
    PROF_OPEN,
    ABOUT_NAV,
}


def is_game_prefix(prefix: str) -> bool:
    return prefix in GAME_PREFIXES


def is_lobby_prefix(prefix: str) -> bool:
    return prefix in LOBBY_PREFIXES
