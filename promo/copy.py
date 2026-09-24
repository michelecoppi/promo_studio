"""Testi di accompagnamento: didascalia, hashtag, commento fissato, link tracciato.

Tutto da template versionati (promo/catalog/*.json); nessun modello linguistico. Se un
giorno si vorra' variare i testi con un LLM, dovra' restare opzionale: il testo passa
comunque dall'approvazione umana (campo `caption` modificabile in admin).

Il link porta `?start=src_<canale>`: e' cosi' che il bot attribuisce il nuovo giocatore al
canale (handlers/start_handler.py nel gioco). Un valore che il bot non conosce viene contato
come "other", quindi `check_sources()` segnala i canali non ancora registrati in
`services/product_analytics.py::CAMPAIGN_SOURCES`.
"""
import random

from promo import catalog
from promo.config import BOT_USERNAME
from promo.models import Card, Copy

MAX_CAPTION = 150
MIN_HASHTAGS, MAX_HASHTAGS = 3, 5

# Canale del post -> valore `src_` del link. Il canale Telegram del gioco non e' ancora fra
# le sorgenti del bot: finche' non viene aggiunto conta come "other" (vedi check_sources).
SOURCE_FOR_CHANNEL = {
    "tiktok": "tiktok",
    "telegram_channel": "telegram_channel",
    "x": "x",
}


def tracking_link(channel: str) -> str:
    source = SOURCE_FOR_CHANNEL.get(channel, channel)
    return f"https://t.me/{BOT_USERNAME}?start=src_{source}"


def check_sources(campaign_sources) -> list:
    """I canali il cui `src_` il bot non riconosce ancora (verrebbero contati come "other")."""
    known = set(campaign_sources or ())
    return sorted(channel for channel, source in SOURCE_FOR_CHANNEL.items() if source not in known)


def _fit(text: str, limit: int = MAX_CAPTION) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _names(cards) -> str:
    return ", ".join(f"{i}) {card.player_name}" for i, card in enumerate(cards, start=1))


def build(fmt: str, lang: str, channel: str, cards: list, *, seed: str = "", sponsored: bool = False) -> Copy:
    """I testi di un contenuto. `cards` e' una lista: un percorso, o quattro per `ladder`."""
    texts = catalog.copy(lang)
    rng = random.Random(f"{seed}|copy|{fmt}|{lang}|{channel}")
    card: Card = cards[0]
    link = tracking_link(channel)
    values = {
        "n": len(card.stops),
        "p": card.percent_solved if card.percent_solved is not None else "",
        "name": card.player_name,
        "names": _names(cards),
        "link": link,
    }

    caption = rng.choice(texts["captions"][fmt]).format(**values)
    if sponsored:
        caption = f"{texts['sponsored']} · {caption}"
    caption = _fit(caption)

    hashtags = list(texts["hashtags"]["base"])
    extra = list(texts["hashtags"]["extra"])
    rng.shuffle(extra)
    hashtags = (hashtags + extra[:1])[:MAX_HASHTAGS]

    if fmt == "ladder":
        pinned = texts["pinned_comment_ladder"].format(**values)
    elif fmt == "solution":
        pinned = texts["pinned_comment_solution"].format(**values)
    else:
        pinned = texts["pinned_comment"].format(**values)

    return Copy(caption=caption, hashtags=hashtags, pinned_comment=pinned, tracking_link=link)


def creator_brief(lang: str, channel: str = "creator") -> str:
    """Bozza di messaggio per un creator pagato, da copiare a mano (mai inviata dallo
    strumento). Contiene sempre la dichiarazione di contenuto sponsorizzato (§3.7)."""
    texts = catalog.copy(lang)
    link = f"https://t.me/{BOT_USERNAME}?start=src_{channel}"
    return texts["brief"].format(link=link, sponsored=texts["sponsored"])
