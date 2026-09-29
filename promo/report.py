"""Report settimanale (§5.6): cosa ha prodotto Promo Studio in una settimana, e quanto e' costato.

Sola lettura e **solo contenuti**: la coda `promo_posts` (bozze create, approvate, rifiutate,
pubblicate, fallite, per canale e lingua) e i costi che compila la persona (`PROMO_COSTS_FILE`).
Lo strumento non legge conti pubblicitari e non spende niente.

Le metriche di prodotto (nuovi giocatori, attivazione, ritorno, North Star, attribuzione per
canale e campagna) **non** si calcolano qui: le calcola solo il supervisore
(`michelecoppi/gtp_orchestrator`, workflow Growth) con le definizioni del gioco
(`docs/product-analytics.md`) e le soglie minime, e le manda su Telegram il lunedi'. Due report
con due query diverse darebbero due numeri diversi per la stessa metrica (issue #8).

I conteggi usano la `history` dei post (chi, quando, da, a): un post conta una volta per evento
nella settimana, anche se e' fallito piu' volte. I giorni sono quelli di Roma.
"""
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from promo import log
from promo.models import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
)

ROME = ZoneInfo("Europe/Rome")

# Eventi contati, nell'ordine delle colonne. `draft` = bozza creata.
EVENTS = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED, STATUS_PUBLISHED, STATUS_FAILED)
EVENT_LABELS = {
    STATUS_DRAFT: "bozze create",
    STATUS_APPROVED: "approvati",
    STATUS_REJECTED: "rifiutati",
    STATUS_PUBLISHED: "pubblicati",
    STATUS_FAILED: "falliti",
}
# Campi di audit da usare quando un post non ha `history` (post vecchi o scritti a mano).
FALLBACK_FIELDS = {
    STATUS_DRAFT: "created_at",
    STATUS_APPROVED: "approved_at",
    STATUS_REJECTED: "rejected_at",
    STATUS_PUBLISHED: "published_at",
}

SUPERVISOR_REPO = "michelecoppi/gtp_orchestrator"
SUPERVISOR_GROWTH_URL = f"https://github.com/{SUPERVISOR_REPO}/actions/workflows/growth.yml"
SUPERVISOR_LINE = (
    "Metriche di prodotto (nuovi giocatori, attivazione a 24 ore per canale e campagna, ritorno a 7 giorni, "
    f"North Star, referral): non sono in questo report. Le calcola solo il supervisore (`{SUPERVISOR_REPO}`) "
    "con le definizioni del gioco e le soglie minime, e le manda su Telegram il lunedi' alle 08 "
    f"(review growth, workflow [Growth]({SUPERVISOR_GROWTH_URL}))."
)


@dataclass
class Week:
    start: str
    end: str  # escluso
    # (evento, canale, lingua) -> numero di post
    counts: dict = field(default_factory=dict)

    def total(self, event: str) -> int:
        return sum(n for (e, _, _), n in self.counts.items() if e == event)

    def by_channel(self, event: str) -> dict:
        out: dict = {}
        for (e, channel, _), n in self.counts.items():
            if e == event:
                out[channel] = out.get(channel, 0) + n
        return out

    def rows(self) -> list:
        """(canale, lingua) presenti nella settimana, ordinati."""
        return sorted({(channel, lang) for (_, channel, lang) in self.counts})


@dataclass
class Report:
    week: Week
    previous: Week
    costs: dict  # canale -> euro, settimana del report
    pending: dict  # stato -> post in coda adesso (draft, approved, failed)
    failed_now: list  # post ancora `failed`, con l'errore
    errors: list


def rome_day(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        value = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso[:10] or None
    return value.astimezone(ROME).date().isoformat()


def today_rome(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(ROME).date().isoformat()


def post_events(post: dict) -> set:
    """Gli eventi di un post come coppie (evento, giorno di Roma)."""
    history = post.get("history") or []
    events = set()
    for entry in history:
        target, source = entry.get("to"), entry.get("from")
        day = rome_day(entry.get("at"))
        if not day:
            continue
        if target == STATUS_DRAFT and source is None:
            events.add((STATUS_DRAFT, day))
        elif target in (STATUS_APPROVED, STATUS_REJECTED, STATUS_PUBLISHED) and source != target:
            events.add((target, day))
        elif target == STATUS_FAILED and entry.get("by") == "publisher":
            events.add((STATUS_FAILED, day))  # le modifiche dei testi di un post fallito non contano
    if not history:
        for event, key in FALLBACK_FIELDS.items():
            day = rome_day(post.get(key))
            if day:
                events.add((event, day))
    return events


def count_week(posts: list, start: str, end: str) -> Week:
    week = Week(start, end)
    for post in posts:
        channel = str(post.get("channel") or "?")
        lang = str(post.get("language") or "?")
        for event in {e for e, day in post_events(post) if start <= day < end}:
            key = (event, channel, lang)
            week.counts[key] = week.counts.get(key, 0) + 1
    return week


def load_costs(path, week_start: str) -> dict:
    """`{"2026-09-18": {"tiktok": 12.5, "creator": 40}}`: euro spesi per canale, per settimana
    (chiave = primo giorno della settimana del report)."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in (data.get(week_start) or {}).items()}


def weekly(store, settings, *, end_day: Optional[str] = None, now: Optional[datetime] = None) -> Report:
    end = end_day or today_rome(now)
    start = (date.fromisoformat(end) - timedelta(days=7)).isoformat()
    prev_start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
    errors: list = []
    posts: list = []
    if store is None:
        errors.append("coda promo_posts non raggiungibile: conteggi dei contenuti vuoti")
    else:
        try:
            posts = list(store.list())
        except Exception as e:  # il report esce comunque, con l'avviso
            errors.append(f"lettura di promo_posts fallita: {e}")
    try:
        costs = load_costs(settings.costs_file, start)
    except (OSError, ValueError) as e:
        errors.append(f"file dei costi illeggibile: {e}")
        costs = {}
    pending = {s: sum(1 for p in posts if p.get("status") == s) for s in (STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED)}
    failed_now = sorted((p for p in posts if p.get("status") == STATUS_FAILED), key=lambda p: p.get("id") or "")
    return Report(count_week(posts, start, end), count_week(posts, prev_start, start), costs, pending, failed_now,
                  errors)


def _delta(now: int, before: int) -> str:
    return f"{now - before:+d}"


def to_markdown(report: Report) -> str:
    w, p = report.week, report.previous
    lines = [
        f"# Report Promo Studio — settimana {w.start} → {w.end} (escluso)",
        "",
        "Solo contenuti: numeri dalla coda `promo_posts` (sola lettura) e costi da `costs.json`. Nessuna",
        "spesa viene fatta dallo strumento.",
        "",
        f"> 📈 {SUPERVISOR_LINE}",
        "",
    ]
    if report.errors:
        lines += ["> ⚠️ Dati incompleti:"] + [f"> - {log.scrub(e)}" for e in report.errors] + [""]

    lines += ["## Sintesi", "", "| | questa settimana | settimana prima | Δ |", "| --- | ---: | ---: | ---: |"]
    for event in EVENTS:
        a, b = w.total(event), p.total(event)
        lines.append(f"| {EVENT_LABELS[event]} | {a} | {b} | {_delta(a, b)} |")
    lines.append("")

    lines += ["## Per canale e lingua", "",
              "| canale | lingua | " + " | ".join(EVENT_LABELS[e] for e in EVENTS) + " |",
              "| --- | --- |" + " ---: |" * len(EVENTS)]
    rows = w.rows()
    if not rows:
        lines.append("| — | — |" + " 0 |" * len(EVENTS))
    for channel, lang in rows:
        cells = " | ".join(str(w.counts.get((e, channel, lang), 0)) for e in EVENTS)
        lines.append(f"| {channel} | {lang} | {cells} |")
    lines.append("")

    lines += ["## Costi", ""]
    published = w.by_channel(STATUS_PUBLISHED)
    if report.costs:
        lines += ["| canale | costo € | contenuti pubblicati | € per contenuto |", "| --- | ---: | ---: | ---: |"]
        for channel in sorted(report.costs):
            cost, n = report.costs[channel], published.get(channel, 0)
            lines.append(f"| {channel} | {cost:.2f} | {n} | {f'{cost / n:.2f}' if n else '—'} |")
        lines.append(f"| **totale** | {sum(report.costs.values()):.2f} | | |")
    else:
        lines.append(f"- Nessun costo registrato per la settimana che inizia il {w.start} (`costs.json`).")
    lines.append("")

    lines += ["## Coda adesso", "",
              f"- da approvare: {report.pending.get(STATUS_DRAFT, 0)}",
              f"- approvati in attesa di pubblicazione: {report.pending.get(STATUS_APPROVED, 0)}",
              f"- falliti: {report.pending.get(STATUS_FAILED, 0)}"]
    for post in report.failed_now:
        error = log.scrub(str(post.get("error") or "")).replace("|", "/")[:160]
        lines.append(f"  - `{post.get('id')}`" + (f": {error}" if error else ""))
    lines.append("")
    return "\n".join(lines)


def write(report: Report, reports_dir) -> Path:
    path = Path(reports_dir) / f"promo-report-{report.week.start}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(report), encoding="utf-8")
    return path


def notify_admin(settings, path, session=None) -> None:
    """Il report come file al solo admin (PROMO_ADMIN_CHAT_ID): e' il maintainer, non terzi."""
    import requests

    if not settings.bot_token or not settings.admin_chat_id:
        raise ValueError("per --notify servono BOT_TOKEN e PROMO_ADMIN_CHAT_ID")
    session = session or requests.Session()
    with open(path, "rb") as fh:
        response = session.post(
            f"https://api.telegram.org/bot{settings.bot_token}/sendDocument",
            data={"chat_id": settings.admin_chat_id, "caption": "Report settimanale Promo Studio"},
            files={"document": (Path(path).name, fh, "text/markdown")},
            timeout=60,
        )
    if not response.json().get("ok"):
        raise RuntimeError(log.scrub(f"invio del report fallito: {response.json().get('description')}"))
