import pytest
from fakes import FakeGame
from helpers import NOW, fake_render, settings

from promo import plan, queue, render
from promo.store import JsonFileStore, MemoryStore


@pytest.fixture
def store():
    return MemoryStore({"p1": {"id": "p1", "status": "draft", "caption": "ciao", "history": []}})


def test_happy_path_records_audit(store):
    post = queue.approve(store, "p1", "michele", now=NOW)
    assert post["status"] == "approved"
    assert post["approved_by"] == "michele" and post["approved_at"] == "2026-09-24T10:30:00Z"
    assert post["history"][-1] == {"from": "draft", "to": "approved", "by": "michele", "at": "2026-09-24T10:30:00Z"}
    post = queue.transition(store, "p1", "published", "publisher", now=NOW)
    assert post["status"] == "published" and post["published_at"]


@pytest.mark.parametrize("actor", ["", "publisher", "scheduler", "  "])
def test_only_humans_approve_or_reject(store, actor):
    with pytest.raises(queue.TransitionError):
        queue.approve(store, "p1", actor)
    with pytest.raises(queue.TransitionError):
        queue.reject(store, "p1", actor)
    assert store.get("p1")["status"] == "draft"


def test_draft_cannot_be_published_directly(store):
    with pytest.raises(queue.TransitionError):
        queue.transition(store, "p1", "published", "publisher")
    with pytest.raises(queue.TransitionError):
        queue.transition(store, "p1", "failed", "publisher")


def test_humans_cannot_mark_published(store):
    queue.approve(store, "p1", "michele")
    with pytest.raises(queue.TransitionError):
        queue.transition(store, "p1", "published", "michele")


def test_failed_is_retriable_and_rejected_is_final(store):
    queue.approve(store, "p1", "michele")
    queue.transition(store, "p1", "failed", "publisher", fields={"error": "boom"})
    assert queue.transition(store, "p1", "published", "publisher")["status"] == "published"
    store.create({"id": "p2", "status": "draft", "history": []})
    queue.reject(store, "p2", "michele", reason="brutto")
    with pytest.raises(queue.TransitionError):
        queue.approve(store, "p2", "michele")
    assert store.get("p2")["history"][-1]["note"] == "brutto"


def test_edit_copy_validates_and_audits(store):
    post = queue.edit_copy(store, "p1", "michele", caption="Nuova   didascalia", hashtags=["calcio", "#quiz", "#x"])
    assert post["caption"] == "Nuova didascalia" and post["hashtags"] == ["#calcio", "#quiz", "#x"]
    assert post["edited_by"] == "michele" and "caption" in post["history"][-1]["note"]
    with pytest.raises(ValueError):
        queue.edit_copy(store, "p1", "michele", caption="x" * 151)
    with pytest.raises(ValueError):
        queue.edit_copy(store, "p1", "michele", hashtags=["#a"])
    with pytest.raises(queue.TransitionError):
        queue.edit_copy(store, "p1", "publisher", caption="x")


def test_json_store_persists(tmp_path):
    path = tmp_path / "q.json"
    s = JsonFileStore(path)
    assert s.create({"id": "a", "status": "draft", "history": []})
    assert not s.create({"id": "a", "status": "draft", "history": []})
    queue.approve(s, "a", "michele")
    assert JsonFileStore(path).get("a")["status"] == "approved"


def test_generate_drafts_creates_posts_idempotently(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(render, "render", fake_render(calls))
    game, store = FakeGame(), MemoryStore()
    cfg = settings(tmp_path)
    lines = plan.generate_drafts(cfg, game, store, None, now=NOW)
    posts = store.list()
    # 3 lingue su TikTok + il canale Telegram per l'italiano
    assert len(posts) == 4, lines
    assert {p["channel"] for p in posts if p["language"] == "it"} == {"tiktok", "telegram_channel"}
    assert all(p["status"] == "draft" and p["scheduled_for"] == "2026-09-24T10:00:00Z" for p in posts)
    assert all(p["id"] == f"{p['source_days'][0]}-{p['format']}-{p['language']}-{p['channel']}" for p in posts)
    assert all(p["player_ids"][0] not in {"future", "unused"} for p in posts)
    rendered = len(calls)
    plan.generate_drafts(cfg, game, store, None, now=NOW)
    assert len(store.list()) == 4 and len(calls) == rendered  # niente doppioni, niente rendering


def test_generate_drafts_adds_solution_for_yesterday(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    game, store = FakeGame(today="2026-09-24"), MemoryStore()
    cfg = settings(tmp_path, languages=("it",), telegram_languages=())
    game._today = "2026-09-23"
    plan.generate_drafts(cfg, game, store, None, day="2026-09-23", fmt="who_is", now=NOW)
    (first,) = store.list()
    queue.approve(store, first["id"], "michele")
    game._today = "2026-09-24"
    plan.generate_drafts(cfg, game, store, None, day="2026-09-24", fmt="who_is", now=NOW)
    solutions = [p for p in store.list() if p["format"] == "solution"]
    assert len(solutions) == 1 and solutions[0]["player_ids"] == first["player_ids"]
    # e il giocatore di ieri non si ripete oggi
    today_main = [p for p in store.list() if p["created_for"] == "2026-09-24" and p["format"] == "who_is"]
    assert today_main[0]["player_ids"] != first["player_ids"]


def test_generate_drafts_refuses_past_days(tmp_path):
    with pytest.raises(ValueError):
        plan.generate_drafts(settings(tmp_path), FakeGame(), MemoryStore(), None, day="2026-09-01")


def test_rotation_falls_back_to_who_is(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    game = FakeGame(challenges=[])  # niente percentuali: percent non e' possibile
    store = MemoryStore()
    plan.generate_drafts(settings(tmp_path, languages=("it",)), game, store, None, fmt="percent", now=NOW)
    assert {p["format"] for p in store.list()} == {"who_is"}
