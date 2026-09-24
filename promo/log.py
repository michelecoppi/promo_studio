"""Log senza segreti.

Quando il repository del gioco e' disponibile si passa anche da
`services/observability.py::scrub_text`, che conosce le variabili sensibili del bot. In ogni
caso qui si oscurano i valori dei segreti del Promo Studio e i token nelle URL della Bot API
(`/bot<token>/`), che finirebbero in chiaro dentro un'eccezione di `requests`.
"""
import logging
import re

_BOT_URL_TOKEN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
REDACTED = "[REDACTED]"

_secrets: list = []
_game_scrub = None

logger = logging.getLogger("promo")


def register_secrets(values) -> None:
    for value in values or ():
        if value and len(value) >= 6 and value not in _secrets:
            _secrets.append(value)


def use_game_scrubber(scrub) -> None:
    global _game_scrub
    _game_scrub = scrub


def scrub(text) -> str:
    text = str(text)
    for value in _secrets:
        text = text.replace(value, REDACTED)
    text = _BOT_URL_TOKEN.sub("/bot" + REDACTED, text)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    if _game_scrub is not None:
        try:
            text = _game_scrub(text)
        except Exception:  # un problema nello scrubber non deve far perdere il log
            pass
    return text


def info(message, *args) -> None:
    logger.info(scrub(message % args if args else message))


def warning(message, *args) -> None:
    logger.warning(scrub(message % args if args else message))


def error(message, *args) -> None:
    logger.error(scrub(message % args if args else message))


def configure(level=logging.INFO) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
