"""Lo stato del Promo Studio in un colpo d'occhio: controlli di configurazione, prossimi
lavori pianificati, riepilogo della coda. Lo usano sia `python -m promo doctor` sia la scheda
"Stato" dell'admin, cosi' le due viste non possono raccontare cose diverse.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from promo import copy as promo_copy
from promo.models import STATUS_APPROVED, STATUS_DRAFT, STATUS_FAILED, STATUS_PUBLISHED, STATUSES

ROME = ZoneInfo("Europe/Rome")

OK, WARN, ERROR, OFF = "ok", "warn", "error", "off"


@dataclass
class Check:
    area: str
    name: str
    level: str  # ok | warn | error | off
    detail: str
    fix: str = ""


def checks(settings, game_source=None, game_error: Optional[str] = None) -> list:
    out = []
    out.append(Check(
        "Generale", "Interruttore PROMO_ENABLED",
        OK if settings.enabled else OFF,
        "attivo: bozze e pubblicazione girano" if settings.enabled else "spento: bozze e pubblicazione non fanno niente",
        "" if settings.enabled else "PROMO_ENABLED=true nel .env (o variabile del repository GitHub)",
    ))
    out.append(Check("Generale", "Lingue", OK, ", ".join(settings.languages)))

    if game_source is None:
        out.append(Check("Gioco", "Repository del gioco", ERROR, game_error or "non configurato",
                         "GAME_REPO_PATH = percorso della checkout di guess_the_player_from_the_path"))
    else:
        out.append(Check("Gioco", "Repository del gioco", OK, str(getattr(game_source, "path", "configurato"))))
        font = game_source.title_font_path()
        out.append(Check("Gioco", "Font Barlow Condensed", OK if font else WARN,
                         "trovato" if font else "non trovato: uso il font di ripiego"))
        try:
            missing = promo_copy.check_sources(game_source.campaign_sources())
        except Exception as e:  # pragma: no cover - dipende dal gioco
            missing, detail = [], f"non verificabile: {e}"
        else:
            detail = "tutti i link src_ sono riconosciuti dal bot" if not missing else (
                "non riconosciuti dal bot (contati come 'other'): " + ", ".join(
                    f"src_{promo_copy.SOURCE_FOR_CHANNEL[c]}" for c in missing))
        out.append(Check("Gioco", "Link tracciati", WARN if missing else OK, detail,
                         "aggiungere i valori a CAMPAIGN_SOURCES in services/product_analytics.py del gioco"
                         if missing else ""))

    out.append(Check("Coda", "Archivio promo_posts", OK,
                     "Firestore" if settings.store == "firestore" else f"file locale {settings.local_store_path}"))
    try:
        from promo.render.engine import ffmpeg_exe
        ffmpeg_exe()
        out.append(Check("Video", "ffmpeg", OK, "disponibile"))
    except Exception as e:
        out.append(Check("Video", "ffmpeg", ERROR, str(e), "pip install -r requirements.txt (imageio-ffmpeg)"))

    telegram_needed = bool(settings.telegram_languages)
    if settings.bot_token and settings.telegram_channel_id:
        out.append(Check("Canali", "Telegram (canale del gioco)", OK,
                         f"{settings.telegram_channel_id} · lingue {', '.join(settings.telegram_languages)}"))
    else:
        out.append(Check(
            "Canali", "Telegram (canale del gioco)", WARN if telegram_needed else OFF,
            "mancano " + " e ".join(n for n, v in (("BOT_TOKEN", settings.bot_token),
                                                   ("PROMO_TELEGRAM_CHANNEL_ID", settings.telegram_channel_id)) if not v),
            "crea un canale, aggiungi il bot come admin, imposta le due variabili",
        ))

    tiktok = [n for n, v in (("TIKTOK_CLIENT_KEY", settings.tiktok_client_key),
                             ("TIKTOK_CLIENT_SECRET", settings.tiktok_client_secret),
                             ("refresh token", settings.tiktok_refresh_token or settings.tiktok_refresh_token_secret))
              if not v]
    out.append(Check("Canali", "TikTok (bozze)", OK if not tiktok else WARN,
                     "configurato" if not tiktok else "mancano " + ", ".join(tiktok),
                     "" if not tiktok else "app su developers.tiktok.com con scope video.upload (vedi Guida)"))
    if not tiktok and not settings.tiktok_refresh_token_secret:
        out.append(Check("Canali", "Rotazione token TikTok", WARN,
                         "il refresh token ruotato non viene salvato in Secret Manager",
                         "TIKTOK_REFRESH_TOKEN_SECRET=projects/<p>/secrets/<nome> (o PROMO_TIKTOK_TOKEN_FILE in locale)"))
    out.append(Check("Canali", "X / Threads", OFF if not settings.x_enabled else WARN,
                     "fase successiva: non implementato" if not settings.x_enabled
                     else "abilitato ma non implementato: i post x falliranno"))

    import os
    posthog = bool(os.environ.get("POSTHOG_PERSONAL_API_KEY") and os.environ.get("POSTHOG_PROJECT_ID"))
    out.append(Check("Report", "PostHog (sola lettura)", OK if posthog else WARN,
                     "configurato" if posthog else "mancano POSTHOG_PERSONAL_API_KEY e/o POSTHOG_PROJECT_ID",
                     "" if posthog else "le stesse variabili della dashboard del gioco"))
    out.append(Check("Report", "Invio all'admin", OK if settings.admin_chat_id and settings.bot_token else OFF,
                     "attivo" if settings.admin_chat_id and settings.bot_token else "non configurato (facoltativo)",
                     "" if settings.admin_chat_id else "PROMO_ADMIN_CHAT_ID = il tuo id Telegram"))
    out.append(Check("Report", "File dei costi", OK if settings.costs_file.exists() else WARN,
                     str(settings.costs_file), "" if settings.costs_file.exists() else "crealo dalla scheda Report"))
    return out


def summary(results) -> dict:
    counts = {OK: 0, WARN: 0, ERROR: 0, OFF: 0}
    for check in results:
        counts[check.level] = counts.get(check.level, 0) + 1
    return counts


# ---------------------------------------------------------------------------------------
# Pianificazione
# ---------------------------------------------------------------------------------------

JOBS = (
    ("Bozze del giorno", 7, None, "python -m promo drafts"),
    ("Pubblicazione approvati", 12, None, "python -m promo publish"),
    ("Report settimanale", 9, 4, "python -m promo report --notify"),  # 4 = venerdi'
)


def next_runs(now: Optional[datetime] = None) -> list:
    """[(lavoro, prossima esecuzione in ora di Roma, comando)] ordinati per ora."""
    now = (now or datetime.now(timezone.utc)).astimezone(ROME)
    runs = []
    for name, hour, weekday, command in JOBS:
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        for _ in range(8):
            if candidate > now and (weekday is None or candidate.weekday() == weekday):
                break
            candidate += timedelta(days=1)
        runs.append((name, candidate, command))
    return sorted(runs, key=lambda r: r[1])


def humanize(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"tra {minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"tra {hours} h {minutes:02d} min"
    return f"tra {hours // 24} g {hours % 24} h"


# ---------------------------------------------------------------------------------------
# Coda
# ---------------------------------------------------------------------------------------

def queue_overview(posts: list, now: Optional[datetime] = None) -> dict:
    now_s = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {s: 0 for s in STATUSES}
    for post in posts:
        counts[post.get("status")] = counts.get(post.get("status"), 0) + 1
    waiting = sorted((p for p in posts if p.get("status") == STATUS_DRAFT), key=lambda p: p.get("scheduled_for") or "")
    upcoming = sorted((p for p in posts if p.get("status") == STATUS_APPROVED),
                      key=lambda p: p.get("scheduled_for") or "")
    overdue_drafts = [p for p in waiting if (p.get("scheduled_for") or "") <= now_s]
    failed = sorted((p for p in posts if p.get("status") == STATUS_FAILED),
                    key=lambda p: p.get("scheduled_for") or "", reverse=True)
    published = sorted((p for p in posts if p.get("status") == STATUS_PUBLISHED),
                       key=lambda p: p.get("published_at") or "", reverse=True)
    return {
        "counts": counts,
        "waiting": waiting,
        "overdue_drafts": overdue_drafts,
        "upcoming": upcoming,
        "failed": failed,
        "published": published,
    }


def to_rome(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        value = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso
    return value.astimezone(ROME).strftime("%d/%m %H:%M")
