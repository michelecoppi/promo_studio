import json
import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "dashboard_app.py")


def _post(pid, status, **kw):
    base = {"id": pid, "status": status, "format": "who_is", "language": "it", "channel": "tiktok",
            "caption": "Chi è?", "hashtags": ["#a", "#b", "#c"], "pinned_comment": "x", "tracking_link": "l",
            "created_for": "2026-09-24", "scheduled_for": "2026-09-24T10:00:00Z", "attempts": 0,
            "history": [{"from": None, "to": "draft", "by": "scheduler", "at": "2026-09-24T05:00:00Z"}],
            "source_days": ["2026-09-10"]}
    base.update(kw)
    return base


@pytest.fixture
def app(tmp_path, monkeypatch):
    # L'app di prova sostituisce promo.render.render con un finto e AppTest gira nello stesso
    # processo: monkeypatch lo rimette a posto per gli altri test.
    from promo import render
    monkeypatch.setattr(render, "render", render.render)
    monkeypatch.setenv("PROMO_TEST_DIR", str(tmp_path))
    posts = {
        "d1": _post("d1", "draft"),
        "a1": _post("a1", "approved", approved_by="michele", scheduled_for="2099-01-01T10:00:00Z"),
        "f1": _post("f1", "failed", error="boom", attempts=1),
        "p1": _post("p1", "published", published_at="2026-09-24T10:01:00Z", external_url="https://t.me/c/1"),
    }
    (tmp_path / "q.json").write_text(json.dumps(posts))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    return at, tmp_path


def test_all_tabs_render(app):
    at, _ = app
    assert [t.label for t in at.tabs][:2] == ["🏠 Stato", "📝 Coda (1)"]
    labels = [m.label for m in at.metric]
    assert "📝 Da approvare" in labels and "Pronti ora" in labels
    assert any("Promo Studio attivo" in s.value for s in at.success)
    assert any("pubblicazione fallita" in e.value for e in at.error)


def test_approve_from_queue(app):
    at, tmp = app
    next(b for b in at.button if b.label == "✅ Approva").click().run()
    assert not at.exception
    post = json.loads((tmp / "q.json").read_text())["d1"]
    assert post["status"] == "approved" and post["approved_by"] == "michele"


def test_generate_and_enqueue(app):
    at, tmp = app
    next(b for b in at.button if b.label == "🎬 Genera video").click().run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "➕ Metti in coda come bozza").click().run()
    assert not at.exception, at.exception
    posts = json.loads((tmp / "q.json").read_text())
    assert len(posts) == 5


def test_publish_dry_run_changes_nothing(app):
    at, tmp = app
    before = (tmp / "q.json").read_text()
    next(b for b in at.button if b.label == "🧪 Simula (dry-run)").click().run()
    assert not at.exception, at.exception
    assert (tmp / "q.json").read_text() == before


def test_costs_are_saved(app):
    at, tmp = app
    next(b for b in at.button if b.label == "💾 Salva costi").click().run()
    assert not at.exception
    assert (tmp / "costs.json").exists()
