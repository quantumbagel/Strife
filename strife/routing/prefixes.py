from __future__ import annotations

G_MOVE = "g_move:"
G_SELECT = "g_select:"
R_NAV = "r_nav:"
REMATCH = "rematch:"
REPLAY_NOOP = "replay_noop:"
CAT_NAV = "cat_nav:"
PROF_NAV = "prof_nav:"
PROF_OPEN = "prof_open:"
FORFEIT = "forfeit:"

SERVER_NAV = "server_nav:"
SERVER_CHANNEL = "server_channel:"

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
LOBBY_APPROVE = "lobby_approve:"
LOBBY_DENY = "lobby_deny:"
LOBBY_ADD_BLACKLIST = "lobby_add_blacklist:"
LOBBY_REMOVE_BLACKLIST = "lobby_remove_blacklist:"

ABOUT_NAV = "about_nav:"

SERVER_PREFIXES = {SERVER_NAV, SERVER_CHANNEL}

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
    LOBBY_APPROVE,
    LOBBY_DENY,
    LOBBY_ADD_BLACKLIST,
    LOBBY_REMOVE_BLACKLIST,
}

ALL_PREFIXES = GAME_PREFIXES | LOBBY_PREFIXES | SERVER_PREFIXES | {
    R_NAV,
    REMATCH,
    REPLAY_NOOP,
    CAT_NAV,
    PROF_NAV,
    PROF_OPEN,
    ABOUT_NAV,
    FORFEIT,
}


def is_game_prefix(prefix: str) -> bool:
    return prefix in GAME_PREFIXES


def is_lobby_prefix(prefix: str) -> bool:
    return prefix in LOBBY_PREFIXES
