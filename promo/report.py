"""Report settimanale (§5.6): quali canali portano giocatori, e se vale la pena continuare.

Sola lettura. I numeri vengono da PostHog via `services/product_analytics_query.py` del gioco
(HogQL con la Personal API Key), i contenuti pubblicati dalla coda `promo_posts`, i costi da
un file che compila la persona (`PROMO_COSTS_FILE`): lo strumento non legge conti
pubblicitari e non spende niente, **propone** soltanto.

Regola di spesa: un canale continua se un nuovo giocatore costa meno di 0,50 € **e** almeno
il 20% dei nuovi gioca ancora dopo 7 giorni. La ritenzione a 7 giorni si misura sulla
coorte della settimana *precedente* (quella di questa settimana non ha ancora 7 giorni).

Definizioni (docs/product-analytics.md del gioco):
- nuovi utenti: `bot_started` con `is_new_user = true`, per `acquisition_channel`;
- attivazione: `bot_started` → primo `daily_completed` entro 7 giorni;
- giocatori attivi al giorno: utenti unici su `daily_guess_submitted`;
- leve social: `league_created`, `group_round_started`, `notifications_changed` (attivate).
"""
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from promo import log
from promo.game import AnalyticsError
from promo.models import STATUS_PUBLISHED

MAX_COST_PER_PLAYER = 0.50
MIN_RETENTION_D7 = 0.20
MIN_COHORT = 10
TZ = "Europe/Rome"

SOCIAL_EVENTS = ("league_created", "group_round_started", "notifications_changed")


def _ts(day: str) -> str:
    return f"toDateTime('{day} 00:00:00', '{TZ}')"


def _window(start: str, end: str, column: str = "timestamp") -> str:
    return f"{column} >= {_ts(start)} AND {column} < {_ts(end)}"


def _cohort(start: str, end: str) -> str:
    return (
        "SELECT distinct_id, argMin(properties.acquisition_channel, timestamp) AS channel, "
        "min(timestamp) AS started FROM events "
        f"WHERE event = 'bot_started' AND properties.is_new_user = true AND {_window(start, end)} "
        "GROUP BY distinct_id"
    )


def q_new_users(start, end) -> str:
    return (
        "SELECT properties.acquisition_channel AS channel, count(DISTINCT distinct_id) AS new_users FROM events "
        f"WHERE event = 'bot_started' AND properties.is_new_user = true AND {_window(start, end)} "
        "GROUP BY channel ORDER BY new_users DESC"
    )


def q_activation(start, end) -> str:
    return (
        "SELECT s.channel AS channel, count() AS cohort, "
        "countIf(c.first_done >= s.started AND c.first_done <= s.started + INTERVAL 7 DAY) AS activated "
        f"FROM ({_cohort(start, end)}) AS s LEFT JOIN ("
        "SELECT distinct_id, min(timestamp) AS first_done FROM events "
        f"WHERE event = 'daily_completed' AND timestamp >= {_ts(start)} GROUP BY distinct_id"
        ") AS c ON s.distinct_id = c.distinct_id GROUP BY channel"
    )


def q_retention(start, end) -> str:
    return (
        "SELECT s.channel AS channel, count() AS cohort, "
        "countIf(p.last_play >= s.started + INTERVAL 7 DAY) AS retained "
        f"FROM ({_cohort(start, end)}) AS s LEFT JOIN ("
        "SELECT distinct_id, max(timestamp) AS last_play FROM events "
        f"WHERE event = 'daily_guess_submitted' AND timestamp >= {_ts(start)} GROUP BY distinct_id"
        ") AS p ON s.distinct_id = p.distinct_id GROUP BY channel"
    )


def q_dau(start, end) -> str:
    return (
        f"SELECT toDate(toTimeZone(timestamp, '{TZ}')) AS dau_day, count(DISTINCT distinct_id) AS players FROM events "
        f"WHERE event = 'daily_guess_submitted' AND {_window(start, end)} GROUP BY dau_day ORDER BY dau_day"
    )


def q_social(start, end) -> str:
    events = ", ".join(f"'{e}'" for e in SOCIAL_EVENTS)
    return (
        "SELECT event AS social_event, count() AS total FROM events "
        f"WHERE event IN ({events}) AND {_window(start, end)} "
        "AND (event != 'notifications_changed' OR properties.enabled = true) GROUP BY social_event"
    )


@dataclass
class Week:
    start: str
    end: str  # escluso
    new_users: dict = field(default_factory=dict)
    activation: dict = field(default_factory=dict)  # canale -> (coorte, attivati)
    dau: list = field(default_factory=list)  # [(giorno, giocatori)]
    social: dict = field(default_factory=dict)
    published: dict = field(default_factory=dict)  # canale -> n

    @property
    def total_new(self) -> int:
        return sum(self.new_users.values())

    @property
    def dau_avg(self) -> Optional[float]:
        return sum(n for _, n in self.dau) / len(self.dau) if self.dau else None


@dataclass
class Report:
    week: Week
    previous: Week
    retention: dict  # canale -> (coorte, ancora attivi dopo 7 giorni), coorte della settimana precedente
    costs: dict
    proposals: list
    errors: list


def _pairs(rows) -> dict:
    return {str(r[0] or "other"): int(r[1] or 0) for r in rows or []}


def _triples(rows) -> dict:
    return {str(r[0] or "other"): (int(r[1] or 0), int(r[2] or 0)) for r in rows or []}


def _published(store, start: str, end: str) -> dict:
    counts: dict = {}
    if store is None:
        return counts
    for post in store.list(STATUS_PUBLISHED):
        day = (post.get("published_at") or "")[:10]
        if start <= day < end:
            counts[post["channel"]] = counts.get(post["channel"], 0) + 1
    return counts


def load_costs(path, week_start: str) -> dict:
    """`{"2026-09-18": {"tiktok": 12.5, "creator": 40}}`: euro spesi per canale, per settimana
    (chiave = primo giorno della settimana del report)."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in (data.get(week_start) or {}).items()}


def _collect(game, store, start, end, errors) -> Week:
    week = Week(start, end)

    def run(name, query, parse):
        try:
            return parse(game.hogql(query))
        except AnalyticsError as e:
            errors.append(f"{name} ({start}): {e}")
            return parse([])

    week.new_users = run("nuovi utenti", q_new_users(start, end), _pairs)
    week.activation = run("attivazione", q_activation(start, end), _triples)
    week.dau = run("giocatori attivi", q_dau(start, end), lambda rows: [(str(r[0]), int(r[1])) for r in rows or []])
    week.social = run("leve social", q_social(start, end), _pairs)
    week.published = _published(store, start, end)
    return week


def propose(channels, new_users: dict, retention: dict, costs: dict) -> list:
    proposals = []
    for channel in channels:
        new = new_users.get(channel, 0)
        cost = costs.get(channel, 0.0)
        cohort, retained = retention.get(channel, (0, 0))
        rate = retained / cohort if cohort else None
        cpa = cost / new if new else None
        facts = f"{new} nuovi, costo {cost:.2f} €" + (f" ({cpa:.2f} €/giocatore)" if cpa is not None else "")
        facts += f", D7 {rate:.0%} su {cohort}" if rate is not None else ", D7 non misurabile"
        if cohort < MIN_COHORT and new < MIN_COHORT:
            verdict = "dati insufficienti: continuare a raccogliere, nessun budget in piu'"
        elif cost > 0 and new == 0:
            verdict = "fermare: spesa senza nuovi giocatori"
        elif rate is None:
            verdict = "attendere la ritenzione a 7 giorni prima di decidere"
        elif (cpa or 0.0) < MAX_COST_PER_PLAYER and rate >= MIN_RETENTION_D7:
            verdict = "continuare" + (" (valutare un aumento graduale del budget)" if cost > 0 else "")
        else:
            reasons = []
            if (cpa or 0.0) >= MAX_COST_PER_PLAYER:
                reasons.append(f"costo per giocatore ≥ {MAX_COST_PER_PLAYER:.2f} €")
            if rate < MIN_RETENTION_D7:
                reasons.append(f"D7 < {MIN_RETENTION_D7:.0%}")
            verdict = "ridurre o fermare: " + ", ".join(reasons)
        proposals.append((channel, facts, verdict))
    return proposals


def weekly(game, store, settings, *, end_day: Optional[str] = None) -> Report:
    end = end_day or game.today()
    start = (date.fromisoformat(end) - timedelta(days=7)).isoformat()
    prev_start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
    errors: list = []
    week = _collect(game, store, start, end, errors)
    previous = _collect(game, store, prev_start, start, errors)
    try:
        retention = _triples(game.hogql(q_retention(prev_start, start)))
    except AnalyticsError as e:
        errors.append(f"ritenzione: {e}")
        retention = {}
    costs = load_costs(settings.costs_file, start)
    try:
        campaign = set(game.campaign_sources())
    except Exception:
        campaign = set()
    channels = sorted((campaign & (set(week.new_users) | set(retention))) | set(costs))
    return Report(week, previous, retention, costs, propose(channels, week.new_users, retention, costs), errors)


def _delta(now, before) -> str:
    if now is None or before is None:
        return "—"
    diff = now - before
    if isinstance(now, float) or isinstance(before, float):
        return f"{diff:+.1f}"
    return f"{diff:+d}"


def to_markdown(report: Report) -> str:
    w, p = report.week, report.previous
    lines = [
        f"# Report Promo Studio — settimana {w.start} → {w.end} (escluso)",
        "",
        "Numeri da PostHog (sola lettura) e dalla coda `promo_posts`. Le proposte sono solo proposte:",
        "nessuna spesa viene fatta dallo strumento.",
        "",
    ]
    if report.errors:
        lines += ["> ⚠️ Dati incompleti:"] + [f"> - {log.scrub(e)}" for e in report.errors] + [""]

    lines += ["## Sintesi", "", "| | questa settimana | settimana prima | Δ |", "| --- | ---: | ---: | ---: |"]
    lines.append(f"| nuovi utenti | {w.total_new} | {p.total_new} | {_delta(w.total_new, p.total_new)} |")
    avg_w = round(w.dau_avg, 1) if w.dau_avg is not None else None
    avg_p = round(p.dau_avg, 1) if p.dau_avg is not None else None
    lines.append(f"| giocatori attivi al giorno (media) | {avg_w if avg_w is not None else '—'} | "
                 f"{avg_p if avg_p is not None else '—'} | {_delta(avg_w, avg_p)} |")
    for event in SOCIAL_EVENTS:
        label = {"league_created": "leghe create", "group_round_started": "round di gruppo",
                 "notifications_changed": "notifiche attivate"}[event]
        a, b = w.social.get(event, 0), p.social.get(event, 0)
        lines.append(f"| {label} | {a} | {b} | {_delta(a, b)} |")
    lines.append("")

    lines += ["## Per canale", "",
              "| canale | nuovi | Δ | attivati entro 7 gg | contenuti pubblicati | costo € | €/nuovo | D7 (coorte prec.) |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    channels = sorted(set(w.new_users) | set(p.new_users) | set(w.published) | set(report.costs) | set(report.retention))
    for channel in channels:
        new = w.new_users.get(channel, 0)
        cohort, activated = w.activation.get(channel, (0, 0))
        act = f"{activated}/{cohort} ({activated / cohort:.0%})" if cohort else "—"
        cost = report.costs.get(channel)
        cpa = f"{cost / new:.2f}" if cost is not None and new else "—"
        r_cohort, retained = report.retention.get(channel, (0, 0))
        ret = f"{retained}/{r_cohort} ({retained / r_cohort:.0%})" if r_cohort else "—"
        published = w.published.get(channel, 0)
        lines.append(f"| {channel} | {new} | {_delta(new, p.new_users.get(channel, 0))} | {act} | {published} | "
                     f"{f'{cost:.2f}' if cost is not None else '—'} | {cpa} | {ret} |")
    lines.append("")

    if w.dau:
        lines += ["## Giocatori attivi al giorno", "", "| giorno | giocatori |", "| --- | ---: |"]
        lines += [f"| {day} | {n} |" for day, n in w.dau] + [""]

    lines += ["## Proposte", "",
              f"Regola: si continua se un nuovo giocatore costa < {MAX_COST_PER_PLAYER:.2f} € e almeno il "
              f"{MIN_RETENTION_D7:.0%} gioca ancora dopo 7 giorni.", ""]
    if not report.proposals:
        lines.append("- Nessun canale di campagna con dati questa settimana.")
    for channel, facts, verdict in report.proposals:
        lines.append(f"- **{channel}**: {verdict} — {facts}")
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
