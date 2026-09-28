"""Brief del supervisore (gtp_orchestrator, M4): `python -m promo brief-import <file.json>`.

Il supervisore prepara ogni settimana un brief con pubblico, lingua, formato, canale, CTA,
angolo e fatti verificati. Qui diventa una bozza come le altre, con due differenze:

- formato, lingua e canale vengono dal brief invece che dalla rotazione (`plan.ROTATION`);
- il post porta `brief_id` (= `campaign_id`) e, se il gioco lo supporta
  (`PROMO_CAMPAIGN_LINKS`), il link `?start=src_<canale>-<campaign_id>`.

Tutto il resto non cambia: le schede le sceglie il picker anti-spoiler, la didascalia viene
dai template versionati (CTA, angolo e fatti restano nel campo `brief`, a disposizione di chi
approva, ma non entrano nel testo) e la bozza nasce `draft`: l'approvazione resta umana.

Idempotente: stesso `campaign_id` e stesso giorno → nessun doppione.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from promo import plan, render
from promo.config import LANGUAGES
from promo.copy import SOURCE_FOR_CHANNEL
from promo.models import CHANNELS, FORMATS
from promo.store import PostStore

# La soluzione nasce solo dall'indovinello del giorno prima: non si chiede con un brief.
BRIEF_FORMATS = tuple(f for f in FORMATS if f != "solution")
# Il parametro `start` di Telegram: al massimo 64 caratteri fra A-Z, a-z, 0-9, _ e -.
START_PARAM_MAX = 64
_CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class BriefError(ValueError):
    pass


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BriefError(f"'{key}' deve essere un testo")
    return value.strip()


def _choice(data: dict, key: str, allowed) -> str:
    value = _text(data, key)
    if value not in allowed:
        raise BriefError(f"'{key}' = {value!r} non e' valido: ammessi {', '.join(allowed)}")
    return value


def validate(data) -> dict:
    """Il brief ripulito, o `BriefError` con un messaggio che dice cosa correggere."""
    if not isinstance(data, dict):
        raise BriefError("il brief deve essere un oggetto JSON")
    campaign_id = _text(data, "campaign_id")
    if not campaign_id:
        raise BriefError("manca 'campaign_id'")
    if not _CAMPAIGN_ID.match(campaign_id):
        raise BriefError(f"'campaign_id' = {campaign_id!r}: solo lettere, cifre, '_' e '-' "
                         "(e deve iniziare con una lettera o una cifra)")
    language = _choice(data, "language", LANGUAGES)
    fmt = _choice(data, "format", BRIEF_FORMATS)
    channel = _choice(data, "channel", CHANNELS)
    start = f"src_{SOURCE_FOR_CHANNEL.get(channel, channel)}-{campaign_id}"
    if len(start) > START_PARAM_MAX:
        raise BriefError(f"'campaign_id' troppo lungo: il parametro del link ({start}) supera "
                         f"{START_PARAM_MAX} caratteri")
    facts = data.get("facts") or []
    if not isinstance(facts, list) or not all(isinstance(f, str) for f in facts):
        raise BriefError("'facts' deve essere una lista di testi")
    day = _text(data, "day")
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise BriefError(f"'day' = {day!r} non e' una data YYYY-MM-DD") from None
    return {
        "campaign_id": campaign_id,
        "language": language,
        "format": fmt,
        "channel": channel,
        "cta": _text(data, "cta"),
        "angle": _text(data, "angle"),
        "facts": [f.strip() for f in facts if f.strip()],
        "day": day or None,
    }


def load(path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BriefError(f"file del brief non trovato: {path}") from None
    except json.JSONDecodeError as e:
        raise BriefError(f"{path} non e' JSON valido: {e}") from None
    return validate(data)


def import_brief(settings, game, store: PostStore, theme, brief: dict, *, day: Optional[str] = None,
                 dry_run: bool = False, now: Optional[datetime] = None) -> list:
    """La bozza di un brief gia' validato. Ritorna righe leggibili su cosa e' stato fatto."""
    today = game.today()
    day = day or brief.get("day") or today
    if day < today:
        raise BriefError(f"{day} e' gia' passato")
    campaign_id, lang, channel = brief["campaign_id"], brief["language"], brief["channel"]

    # Il controllo e' sulla campagna, non sull'id: rilanciando, l'esclusione degli ultimi
    # 30 giorni sceglierebbe un altro giocatore e quindi un altro id.
    already = [p for p in store.list() if p.get("brief_id") == campaign_id and p.get("created_for") == day]
    if already:
        return [f"{campaign_id}: bozze del {day} gia' presenti ({', '.join(sorted(p['id'] for p in already))})"]

    seed = f"{day}|{lang}|{campaign_id}"
    actual, cards = plan._choose(game, brief["format"], lang, seed, plan._recent_players(store, lang, day))
    media = render.render(actual, cards, lang, theme, Path(settings.media_dir) / day)
    post = plan.make_post(fmt=actual, lang=lang, channel=channel, cards=cards, day=day, media=media, seed=seed,
                          now=now, brief=brief, campaign_links=settings.campaign_links)
    created = False if dry_run else store.create(post)
    state = "creata" if created else "non scritta" if dry_run else "gia esistente"
    return [f"{campaign_id}: {'(dry-run) ' if dry_run else ''}{post['id']} {state} ({post['tracking_link']})"]
