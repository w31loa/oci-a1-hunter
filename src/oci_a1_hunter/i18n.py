"""Tiny two-language helper: Russian if the system locale is Russian, English otherwise."""

from __future__ import annotations

import locale
import os


def _detect() -> str:
    for var in ("A1_HUNTER_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    try:
        loc = (locale.getlocale()[0] or "").lower()
    except ValueError:
        loc = ""
    return "ru" if loc.startswith(("ru", "russian")) else "en"


LANG = _detect()


def set_lang(lang: str) -> None:
    global LANG
    LANG = "ru" if lang.lower().startswith("ru") else "en"


def t(en: str, ru: str) -> str:
    return ru if LANG == "ru" else en
