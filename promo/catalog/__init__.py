"""Testi per lingua (IT/EN/ES): nessuna stringa visibile vive nel codice.

I file JSON sono versionati e si modificano senza toccare Python. `tests/test_catalog.py`
controlla che le tre lingue abbiano le stesse chiavi e gli stessi segnaposto.
"""
import json
from functools import lru_cache
from pathlib import Path

from promo.config import LANGUAGES

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load(lang: str) -> dict:
    if lang not in LANGUAGES:
        raise ValueError(f"lingua non supportata: {lang!r} (ammesse: {', '.join(LANGUAGES)})")
    with open(_DIR / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


def video(lang: str) -> dict:
    return load(lang)["video"]


def copy(lang: str) -> dict:
    return load(lang)["copy"]
