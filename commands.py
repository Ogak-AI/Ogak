"""
commands.py — Ogak special command handlers.
Handles: STOP / PRIVACYOFF (opt-out), START / PRIVACYON (opt-in),
         HELP, and first-time user welcome detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("ogak.commands")

OPTOUT_FILE = os.getenv("OPTOUT_FILE", "optout.json")
SEEN_USERS_FILE = os.getenv("SEEN_USERS_FILE", "seen_users.json")
SHORTCODE = os.getenv("SHORTCODE", "55555")

# ── Welcome message (128 chars — one GSM-7 segment) ──────────────────────────
WELCOME_MSG = (
    "Hi! I'm Ogak, ur free AI on {sc}. "
    "Anon usage logged 4 research. "
    "Text STOP 2 opt out. "
    "Ask me anything!"
).format(sc=SHORTCODE)[:140]

HELP_MSG = (
    "Ogak cmds: STOP=opt out | START=opt back in | "
    "HELP=this msg. Otherwise just ask me anything - na free!"
)[:140]

OPTOUT_ACK = "U don opt out. Ogak no go log ur chats again. Text START to come back."[:140]
OPTIN_ACK  = "Welcome back! Ogak don add u again. Ask me anything, free!"[:140]


def _load_json_set(path: str) -> set:
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_json_set(path: str, data: set) -> None:
    Path(path).write_text(json.dumps(list(data)), encoding="utf-8")


def phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.strip().encode()).hexdigest()[:16]


# ── Opt-out store ─────────────────────────────────────────────────────────────

def is_opted_out(phone: str) -> bool:
    return phone_hash(phone) in _load_json_set(OPTOUT_FILE)


def opt_out(phone: str) -> None:
    store = _load_json_set(OPTOUT_FILE)
    store.add(phone_hash(phone))
    _save_json_set(OPTOUT_FILE, store)
    logger.info("User opted out: hash=%s", phone_hash(phone))


def opt_in(phone: str) -> None:
    store = _load_json_set(OPTOUT_FILE)
    store.discard(phone_hash(phone))
    _save_json_set(OPTOUT_FILE, store)
    logger.info("User opted in: hash=%s", phone_hash(phone))


# ── New-user detection ────────────────────────────────────────────────────────

def is_new_user(phone: str) -> bool:
    return phone_hash(phone) not in _load_json_set(SEEN_USERS_FILE)


def mark_seen(phone: str) -> None:
    store = _load_json_set(SEEN_USERS_FILE)
    store.add(phone_hash(phone))
    _save_json_set(SEEN_USERS_FILE, store)


# ── Command dispatcher ────────────────────────────────────────────────────────

STOP_KEYWORDS  = {"stop", "privacyoff", "optout", "opt out", "unsubscribe"}
START_KEYWORDS = {"start", "privacyon", "optin", "opt in", "subscribe"}
HELP_KEYWORDS  = {"help", "info", "commands", "cmd"}


def check_command(phone: str, text: str) -> str | None:
    """
    Returns a ready-to-send reply string if the message is a special command,
    or None if it should be processed by the LLM.
    Also handles new-user welcome injection.
    """
    clean = text.strip().lower()

    # --- Opt-out ---
    if clean in STOP_KEYWORDS:
        opt_out(phone)
        return OPTOUT_ACK

    # --- Opt-in ---
    if clean in START_KEYWORDS:
        opt_in(phone)
        return OPTIN_ACK

    # --- Help ---
    if clean in HELP_KEYWORDS:
        return HELP_MSG

    # --- New user welcome (prepend silently; LLM still runs) ---
    # Caller checks is_new_user() separately to send welcome first.
    return None
