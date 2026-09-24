"""I due lavori quotidiani: preparare le bozze (07:00) e pubblicare gli approvati (12:00).

**Bozze.** Per ogni lingua: un video nel formato del giorno (a rotazione, vedi `ROTATION`)
e, se ieri c'era un indovinello in quella lingua, il video `solution` che lo risolve. Ogni
video diventa un post per canale (TikTok per tutte le lingue, il canale Telegram per quelle
in `PROMO_TELEGRAM_LANGUAGES`). Gli id sono deterministici e la creazione e' "solo se non
esiste", quindi rilanciare il lavoro non crea doppioni.

**Pubblicazione.** Solo post `approved` (o `failed`, riprovabili) con `scheduled_for`
passato. Ogni post si "prende" con un confronto-e-scrivi atomico prima di chiamare il
canale; un errore diventa `failed` con il motivo e non ferma gli altri post.

Il video non viaggia fra le due esecuzioni: se il file non c'e' (il workflow delle 12:00 gira
su un'altra macchina) si rigenera da `render_spec`, identico perche' il rendering e'
deterministico, e si controlla lo sha256.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from promo import copy as promo_copy
from promo import log, picker, queue, render
from promo.models import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    Card,
    post_id,
)
from promo.store import PostStore, now_iso

ROME = ZoneInfo("Europe/Rome")

# Lunedi' = 0. Il venerdi' la scala: e' il formato piu' lungo e il piu' "da weekend".
ROTATION = ("who_is", "percent", "journeyman", "who_is", "ladder", "percent", "who_is")
PUBLISH_HOUR = 12
RECENT_DAYS = 30
MAX_ATTEMPTS = 3
LOCK_MINUTES = 30
SOLVABLE = ("who_is", "percent", "journeyman", "ladder")


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def scheduled_for(day: str, hour: int = PUBLISH_HOUR) -> str:
    local = datetime.fromisoformat(day).replace(hour=hour, tzinfo=ROME)
    return now_iso(local)


def channels_for(settings, lang: str) -> list:
    channels = ["tiktok"]
    if lang in settings.telegram_languages:
        channels.append("telegram_channel")
    if settings.x_enabled:
        channels.append("x")
    return channels


def format_for(day: str) -> str:
    return ROTATION[date.fromisoformat(day).weekday()]


def _recent_players(store: PostStore, lang: str, day: str) -> set:
    since = _shift(day, -RECENT_DAYS)
    used = set()
    for post in store.list():
        if post.get("language") == lang and (post.get("created_for") or "") >= since:
            used.update(post.get("player_ids") or ())
    return used


def _choose(game, fmt: str, lang: str, seed: str, exclude: set) -> tuple:
    """(formato effettivo, schede). Se il formato del giorno non ha materiale si ripiega su
    `who_is`, che ne ha sempre (sfide passate + pool)."""
    try:
        if fmt == "ladder":
            return fmt, picker.pick_ladder(game, lang, seed=seed, exclude_player_ids=exclude)
        return fmt, [picker.pick(game, fmt, lang, seed=seed, exclude_player_ids=exclude)]
    except picker.NoMaterial as e:
        if fmt == "who_is":
            raise
        log.warning("nessun materiale per %s (%s): ripiego su who_is", fmt, e)
        return "who_is", [picker.pick(game, "who_is", lang, seed=seed, exclude_player_ids=exclude)]


def _source_days(cards) -> list:
    return [c.source_day or c.source_key for c in cards]


def make_post(*, fmt: str, lang: str, channel: str, cards: list, day: str, media, seed: str,
              now: Optional[datetime] = None, origin: Optional[str] = None) -> dict:
    texts = promo_copy.build(fmt, lang, channel, cards, seed=seed)
    origin = origin or (day if fmt == "ladder" else cards[0].origin)
    return {
        "id": post_id(origin, fmt, lang, channel),
        "status": STATUS_DRAFT,
        "format": fmt,
        "language": lang,
        "channel": channel,
        "source_days": _source_days(cards),
        "player_ids": [c.player_id for c in cards],
        "media_path": str(media.video_path),
        "cover_path": str(media.cover_path),
        "media_sha256": media.sha256,
        "duration": media.duration,
        "caption": texts.caption,
        "hashtags": texts.hashtags,
        "pinned_comment": texts.pinned_comment,
        "tracking_link": texts.tracking_link,
        "scheduled_for": scheduled_for(day),
        "created_for": day,
        "created_at": now_iso(now),
        "approved_at": None,
        "approved_by": None,
        "published_at": None,
        "external_id": None,
        "external_url": None,
        "error": "",
        "attempts": 0,
        "history": [{"from": None, "to": STATUS_DRAFT, "by": "scheduler", "at": now_iso(now)}],
        "render_spec": {
            "format": fmt,
            "language": lang,
            "cards": [c.to_dict() for c in cards],
            "basename": Path(media.video_path).stem,
        },
    }


def generate_drafts(settings, game, store: PostStore, theme, *, day: Optional[str] = None,
                    fmt: Optional[str] = None, dry_run: bool = False, now: Optional[datetime] = None) -> list:
    """Le bozze del giorno. Ritorna righe leggibili su cosa e' stato fatto."""
    today = game.today()
    day = day or today
    # Le bozze non si preparano per giorni passati: il seme e l'esclusione "ultimi 30 giorni"
    # sono pensati per andare avanti, e una bozza nel passato sarebbe gia' scaduta.
    if day < today:
        raise ValueError(f"{day} e' gia' passato")
    lines = []
    media_dir = Path(settings.media_dir) / day
    existing = store.list()

    for lang in settings.languages:
        channels = channels_for(settings, lang)
        seed = f"{day}|{lang}"
        already = [p for p in existing if p.get("created_for") == day and p.get("language") == lang
                   and p.get("format") != "solution"]
        if already:
            lines.append(f"{lang}: bozze del {day} gia' presenti ({', '.join(sorted(p['id'] for p in already))})")
        else:
            wanted = fmt or format_for(day)
            actual, cards = _choose(game, wanted, lang, seed, _recent_players(store, lang, day))
            media = render.render(actual, cards, lang, theme, media_dir)
            for channel in channels:
                post = make_post(fmt=actual, lang=lang, channel=channel, cards=cards, day=day, media=media,
                                 seed=seed, now=now)
                created = False if dry_run else store.create(post)
                lines.append(f"{lang}: {'(dry-run) ' if dry_run else ''}{post['id']} "
                             f"{'creata' if created else 'non scritta' if dry_run else 'gia esistente'}")

        # La soluzione dell'indovinello di ieri, se c'era e non era stato rifiutato.
        yesterday = _shift(day, -1)
        solved_cards = {}
        for post in existing:
            if (post.get("created_for") == yesterday and post.get("language") == lang
                    and post.get("format") in SOLVABLE and post.get("status") in (STATUS_APPROVED, STATUS_PUBLISHED)
                    and post.get("format") != "ladder"):  # la scala si risolve nel commento fissato
                solved_cards.setdefault(post["player_ids"][0], (post, []))[1].append(post["channel"])
        for player_id, (source_post, source_channels) in sorted(solved_cards.items()):
            card = Card.from_dict(source_post["render_spec"]["cards"][0])
            media = render.render("solution", [card], lang, theme, media_dir)
            for channel in sorted(set(source_channels)):
                post = make_post(fmt="solution", lang=lang, channel=channel, cards=[card], day=day, media=media,
                                 seed=seed, now=now)
                created = False if dry_run else store.create(post)
                lines.append(f"{lang}: {'(dry-run) ' if dry_run else ''}{post['id']} "
                             f"{'creata' if created else 'non scritta' if dry_run else 'gia esistente'}")
    return lines


# ---------------------------------------------------------------------------------------
# Pubblicazione
# ---------------------------------------------------------------------------------------

def ensure_media(post: dict, theme, media_dir) -> str:
    """Il file del video, rigenerato da `render_spec` se su questa macchina non c'e'."""
    path = post.get("media_path") or ""
    if path and os.path.exists(path):
        return path
    spec = post.get("render_spec") or {}
    if not spec:
        raise FileNotFoundError(f"video di {post['id']} assente e senza render_spec")
    cards = [Card.from_dict(c) for c in spec["cards"]]
    out = Path(media_dir) / (post.get("created_for") or "regenerated")
    result = render.render(spec["format"], cards, spec["language"], theme, out, basename=spec.get("basename", ""))
    expected = post.get("media_sha256")
    if expected and result.sha256 != expected:
        # Stesso contenuto, byte diversi (altra versione di ffmpeg o del font): il video e'
        # comunque quello approvato, ma lo si annota.
        log.warning("%s: video rigenerato con sha256 diverso (%s ≠ %s)", post["id"], result.sha256[:12], expected[:12])
    return result.video_path


def _due(post: dict, now_s: str) -> bool:
    return (post.get("scheduled_for") or "") <= now_s


def _lock_free(post: dict, now: datetime) -> bool:
    lock = post.get("publishing_since")
    if not lock:
        return True
    started = datetime.strptime(lock, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return now - started > timedelta(minutes=LOCK_MINUTES)


def publish_due(store: PostStore, publishers: dict, theme, settings, *, dry_run: bool = False,
                only_id: Optional[str] = None, now: Optional[datetime] = None) -> list:
    now = now or datetime.now(timezone.utc)
    now_s = now_iso(now)
    lines = []
    candidates = store.list(STATUS_APPROVED) + [
        p for p in store.list(STATUS_FAILED) if int(p.get("attempts") or 0) < MAX_ATTEMPTS
    ]
    for post in sorted(candidates, key=lambda p: (p.get("scheduled_for") or "", p["id"])):
        if only_id and post["id"] != only_id:
            continue
        if not _due(post, now_s):
            continue
        if post.get("external_id"):
            # Gia' uscito (per esempio: pubblicato, poi l'aggiornamento di stato e' fallito).
            lines.append(f"{post['id']}: ha gia' un external_id, non si ripubblica")
            continue
        publisher = publishers.get(post["channel"])
        if publisher is None:
            lines.append(f"{post['id']}: nessun publisher per {post['channel']} (lasciato in coda)")
            continue
        try:
            lines.append(_publish_one(store, publisher, post, theme, settings, now, dry_run))
        except Exception as e:  # mai fermare il batch
            lines.append(f"{post['id']}: errore inatteso {log.scrub(e)}")
    return lines or ["niente da pubblicare"]


def _publish_one(store, publisher, post, theme, settings, now, dry_run) -> str:
    try:
        media = ensure_media(post, theme, settings.media_dir)
    except Exception as e:
        if dry_run:
            return f"{post['id']}: (dry-run) video non disponibile: {log.scrub(e)}"
        return _fail(store, post, f"video non disponibile: {e}", now)

    if dry_run:
        plan = publisher.describe(post, media)
        return f"{post['id']}: (dry-run) {plan}"

    status = post["status"]
    claimed = store.claim(
        post["id"],
        lambda p: p["status"] == status and not p.get("external_id") and _lock_free(p, now),
        {"publishing_since": now_iso(now), "attempts": int(post.get("attempts") or 0) + 1},
    )
    if claimed is None:
        return f"{post['id']}: preso da un'altra esecuzione o cambiato, saltato"

    try:
        result = publisher.publish(claimed, media)
    except Exception as e:  # un publisher non deve mai far saltare il batch
        result = None
        error = f"{type(e).__name__}: {e}"
    else:
        error = result.error if not result.ok else ""

    if result is not None and result.ok:
        queue.transition(store, post["id"], STATUS_PUBLISHED, "publisher", now=now, fields={
            "external_id": result.external_id,
            "external_url": result.external_url,
            "publishing_since": None,
        })
        return f"{post['id']}: pubblicato ({result.external_url or result.external_id})"
    return _fail(store, claimed, error, now)


def _fail(store, post, error, now) -> str:
    error = log.scrub(error)[:500]
    queue.transition(store, post["id"], STATUS_FAILED, "publisher", now=now, note=error,
                     fields={"error": error, "publishing_since": None})
    return f"{post['id']}: failed ({error})"
