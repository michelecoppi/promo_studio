import json
from datetime import datetime, timezone
from pathlib import Path

from helpers import settings

from promo import report
from promo.store import MemoryStore


def h(source, target, by, at, note=""):
    entry = {"from": source, "to": target, "by": by, "at": at}
    if note:
        entry["note"] = note
    return entry


def post(pid, status, channel, lang, history, **extra):
    return {"id": pid, "status": status, "channel": channel, "language": lang, "history": history, **extra}


def queue():
    return MemoryStore({
        # Pubblicato questa settimana (bozza e approvazione la settimana prima).
        "a": post("a", "published", "tiktok", "it", [
            h(None, "draft", "scheduler", "2026-09-15T06:37:00Z"),
            h("draft", "approved", "Michele", "2026-09-16T08:00:00Z"),
            h("approved", "published", "publisher", "2026-09-17T10:23:00Z"),
        ]),
        # Fallito due volte questa settimana, testi modificati: conta una volta sola come fallito.
        "b": post("b", "failed", "tiktok", "en", [
            h(None, "draft", "scheduler", "2026-09-18T06:37:00Z"),
            h("draft", "approved", "Michele", "2026-09-18T09:00:00Z"),
            h("approved", "failed", "publisher", "2026-09-18T10:23:00Z"),
            h("failed", "failed", "publisher", "2026-09-19T10:23:00Z"),
            h("failed", "failed", "Michele", "2026-09-19T11:00:00Z", "testi modificati: caption"),
        ], error="upload rifiutato | quota"),
        "c": post("c", "rejected", "telegram_channel", "it", [
            h(None, "draft", "scheduler", "2026-09-20T06:37:00Z"),
            h("draft", "rejected", "Michele", "2026-09-20T09:00:00Z"),
        ]),
        "d": post("d", "draft", "telegram_channel", "it", [h(None, "draft", "scheduler", "2026-09-23T06:37:00Z")]),
        # Mezzanotte UTC del 24 = 02:00 a Roma del 24: fuori dalla settimana (fine esclusa).
        "e": post("e", "draft", "tiktok", "es", [h(None, "draft", "scheduler", "2026-09-23T22:30:00Z")]),
        # Senza history (post vecchio): si usano i campi di audit.
        "f": post("f", "published", "tiktok", "it", [], created_at="2026-09-12T06:37:00Z",
                  approved_at="2026-09-12T09:00:00Z", published_at="2026-09-13T10:23:00Z"),
    })


def test_weekly_counts_contents_by_channel_and_language(tmp_path):
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"2026-09-17": {"tiktok": 10.0, "creator": 40.0}}))
    result = report.weekly(queue(), settings(tmp_path, costs_file=costs), end_day="2026-09-24")
    w, p = result.week, result.previous
    assert (w.start, w.end, p.start) == ("2026-09-17", "2026-09-24", "2026-09-10")
    assert w.counts == {
        ("published", "tiktok", "it"): 1,
        ("draft", "tiktok", "en"): 1, ("approved", "tiktok", "en"): 1, ("failed", "tiktok", "en"): 1,
        ("draft", "telegram_channel", "it"): 2, ("rejected", "telegram_channel", "it"): 1,
    }
    assert p.total("draft") == 2 and p.total("approved") == 2 and p.total("published") == 1
    assert result.pending == {"draft": 2, "approved": 0, "failed": 1}
    assert [x["id"] for x in result.failed_now] == ["b"]
    assert result.errors == []

    md = report.to_markdown(result)
    assert "| pubblicati | 1 | 1 | +0 |" in md
    assert "| bozze create | 3 | 2 | +1 |" in md
    assert "| tiktok | en | 1 | 1 | 0 | 0 | 1 |" in md
    assert "| tiktok | 10.00 | 1 | 10.00 |" in md and "| creator | 40.00 | 0 | — |" in md
    assert "upload rifiutato / quota" in md  # niente `|` che rompe la tabella
    assert "Nessuna\nspesa" in md or "nessuna spesa" in md.lower()
    path = report.write(result, tmp_path / "reports")
    assert path.name == "promo-report-2026-09-17.md"


def test_report_points_to_the_supervisor_for_product_metrics(tmp_path):
    md = report.to_markdown(report.weekly(MemoryStore({}), settings(tmp_path), end_day="2026-09-24"))
    assert "gtp_orchestrator" in md and "Telegram il lunedi'" in md
    assert "https://github.com/michelecoppi/gtp_orchestrator/actions/workflows/growth.yml" in md
    # Niente metriche di prodotto calcolate qui (issue #8).
    for word in ("nuovi utenti", "D7", "attivati", "giocatori attivi al giorno"):
        assert word not in md


def test_report_has_no_funnel_queries():
    """Issue #8: nessuna query di funnel duplicata in Promo, le metriche di prodotto sono del supervisore."""
    source = Path(report.__file__).read_text(encoding="utf-8")
    for marker in ("hogql", "SELECT", "bot_started", "daily_completed", "acquisition_channel"):
        assert marker not in source


def test_report_survives_missing_queue(tmp_path):
    result = report.weekly(None, settings(tmp_path), end_day="2026-09-24")
    md = report.to_markdown(result)
    assert result.errors and "Dati incompleti" in md
    assert "Nessun costo registrato" in md

    class Broken(MemoryStore):
        def list(self, status=None):
            raise RuntimeError("Firestore giu'")
    result = report.weekly(Broken(), settings(tmp_path), end_day="2026-09-24")
    assert any("Firestore giu'" in e for e in result.errors)


def test_default_end_is_today_in_rome(tmp_path):
    late = datetime(2026, 9, 23, 22, 30, tzinfo=timezone.utc)  # 00:30 del 24 a Roma
    result = report.weekly(MemoryStore({}), settings(tmp_path), now=late)
    assert result.week.end == "2026-09-24"
