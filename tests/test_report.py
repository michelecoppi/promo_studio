import json

from fakes import FakeGame
from helpers import settings

from promo import report
from promo.game import AnalyticsError
from promo.store import MemoryStore

ROWS = {
    "AS new_users": [["tiktok", 40], ["direct", 5], ["reddit", 8]],
    "AS activated": [["tiktok", 40, 20], ["direct", 5, 4]],
    "AS dau_day": [["2026-09-17", 30], ["2026-09-18", 50]],
    "AS social_event": [["league_created", 3], ["notifications_changed", 7]],
    "AS retained": [["tiktok", 30, 9], ["reddit", 12, 1]],
}


def test_weekly_report_numbers_and_proposals(tmp_path):
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"2026-09-17": {"tiktok": 10.0, "reddit": 8.0}}))
    store = MemoryStore({
        "a": {"id": "a", "status": "published", "channel": "tiktok", "published_at": "2026-09-20T10:00:00Z"},
        "b": {"id": "b", "status": "published", "channel": "tiktok", "published_at": "2026-09-01T10:00:00Z"},
    })
    game = FakeGame(hogql_rows=ROWS)
    result = report.weekly(game, store, settings(tmp_path, costs_file=costs), end_day="2026-09-24")
    assert result.week.start == "2026-09-17" and result.previous.start == "2026-09-10"
    assert result.week.total_new == 53 and result.week.published == {"tiktok": 1}
    verdicts = {c: v for c, _, v in result.proposals}
    assert verdicts["tiktok"].startswith("continuare")          # 0,25 €/nuovo, D7 30%
    assert verdicts["reddit"].startswith("ridurre o fermare")    # 1 €/nuovo, D7 8%
    assert "direct" not in verdicts                              # non e' un canale di campagna
    md = report.to_markdown(result)
    assert "| tiktok | 40 |" in md and "20/40 (50%)" in md and "9/30 (30%)" in md
    assert "nessuna spesa" in md
    # sola lettura: solo SELECT
    assert all(q.lstrip().upper().startswith("SELECT") for q in game.queries)
    path = report.write(result, tmp_path / "reports")
    assert path.name == "promo-report-2026-09-17.md"


def test_proposal_rules():
    p = dict((c, v) for c, _, v in report.propose(
        ["a", "b", "c", "d"],
        {"a": 100, "b": 3, "c": 0, "d": 50},
        {"a": (100, 25), "b": (2, 1), "d": (50, 5)},
        {"a": 40.0, "c": 5.0},
    ))
    assert p["a"].startswith("continuare")
    assert p["b"].startswith("dati insufficienti")
    assert p["c"].startswith("dati insufficienti") or p["c"].startswith("fermare")
    assert p["d"].startswith("ridurre")  # gratis ma D7 10%


def test_report_survives_posthog_down(tmp_path):
    class Down(FakeGame):
        def hogql(self, query):
            raise AnalyticsError("PostHog non configurato")
    result = report.weekly(Down(), None, settings(tmp_path), end_day="2026-09-24")
    assert result.errors and "Dati incompleti" in report.to_markdown(result)
