"""Il contratto di `promo_posts` (docs/schemas/promo_post.v1.json) contro i post veri di Promo.

Il supervisore (michelecoppi/gtp_orchestrator) legge la coda in sola lettura e valida le sue fixture
contro una copia di questo schema. Qui si controlla l'altro lato: ogni documento che Promo scrive
(`make_post`, ogni transizione di `queue`, la pubblicazione, il messaggio di approvazione) rispetta lo
schema. Se un cambio al codice rompe questi test, si aggiorna lo schema (una v2 se il cambio e'
incompatibile) e si apre un'issue su gtp_orchestrator.
"""
import copy as copylib
import json
from pathlib import Path

import pytest
from fakes import FakeGame
from helpers import NOW, fake_render, settings
from jsonschema import Draft202012Validator
from test_approvals import TelegramFake, early, make_settings

from promo import approvals, briefs, config, models, plan, queue, render
from promo.publishers.base import Publisher, PublishResult
from promo.store import MemoryStore

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "promo_post.v1.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def errors(post: dict) -> list:
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in VALIDATOR.iter_errors(post)]


def assert_valid(post: dict) -> None:
    assert errors(post) == [], post["id"]


class FakePublisher(Publisher):
    def __init__(self, channel, ok=True):
        self.channel, self.ok = channel, ok

    def publish(self, post, media_path):
        if not self.ok:
            return PublishResult.failure("HTTP 500")
        return PublishResult(ok=True, external_id=f"{self.channel}:1",
                             external_url=f"https://example.test/{post['id']}")


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    """Le bozze vere del giorno (make_post via generate_drafts) sul gioco finto."""
    monkeypatch.setattr(render, "render", fake_render())
    store = MemoryStore()
    plan.generate_drafts(settings(tmp_path), FakeGame(), store, None, now=NOW)
    assert len(store.list()) == 4
    return store


def test_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_schema_enums_match_the_code():
    props, defs = SCHEMA["properties"], SCHEMA["$defs"]
    assert tuple(defs["status"]["enum"]) == models.STATUSES
    assert tuple(props["format"]["enum"]) == models.FORMATS
    assert tuple(props["channel"]["enum"]) == models.CHANNELS
    assert all(VALIDATOR.is_valid({**_minimal(), "language": lang}) for lang in config.LANGUAGES)
    assert SCHEMA["additionalProperties"] is True  # Promo puo' aggiungere campi senza una v2


def test_drafts_from_make_post_are_valid(drafts):
    for post in drafts.list():
        assert_valid(post)


def test_brief_drafts_are_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    store = MemoryStore()
    brief = briefs.validate({"campaign_id": "w40-it", "language": "it", "format": "who_is",
                             "channel": "tiktok", "cta": "Gioca sul bot", "angle": "giramondo",
                             "facts": ["5 squadre"]})
    briefs.import_brief(settings(tmp_path), FakeGame(), store, None, brief, now=NOW)
    (post,) = store.list()
    assert post["brief_id"] == "w40-it"
    assert_valid(post)


def test_every_queue_transition_is_valid(drafts):
    it_tiktok, it_channel, en, es = sorted(p["id"] for p in drafts.list())
    assert_valid(queue.edit_copy(drafts, it_tiktok, "michele", caption="Chi è?", now=NOW))
    assert_valid(queue.approve(drafts, it_tiktok, "michele", now=NOW))
    assert_valid(queue.reject(drafts, it_channel, "michele", reason="doppione", now=NOW))
    queue.approve(drafts, en, "michele", now=NOW)
    assert_valid(queue.transition(drafts, en, models.STATUS_FAILED, "publisher", now=NOW,
                                  fields={"error": "HTTP 500"}))
    assert_valid(queue.transition(drafts, en, models.STATUS_PUBLISHED, "publisher", now=NOW))
    queue.approve(drafts, es, "michele", now=NOW)
    assert_valid(queue.reject(drafts, es, "michele", now=NOW))  # approved -> rejected
    for post in drafts.list():
        assert_valid(post)


def test_published_and_failed_by_the_publisher_are_valid(drafts, tmp_path):
    for post in drafts.list():
        queue.approve(drafts, post["id"], "michele", now=NOW)
    publishers = {"tiktok": FakePublisher("tiktok"),
                  "telegram_channel": FakePublisher("telegram_channel", ok=False)}
    plan.publish_due(drafts, publishers, None, settings(tmp_path), now=NOW)
    statuses = {p["channel"]: p["status"] for p in drafts.list()}
    assert statuses == {"tiktok": "published", "telegram_channel": "failed"}
    for post in drafts.list():
        assert_valid(post)
        assert post["publishing_since"] is None


def test_approval_message_is_valid(drafts, tmp_path):
    fake = TelegramFake()
    cfg = make_settings(tmp_path)
    approvals.ask(drafts, approvals.build_bot(cfg, fake), None, cfg, now=early())
    posts = drafts.list()
    assert all(isinstance(p["approval_message_id"], int) for p in posts)
    for post in posts:
        assert_valid(post)


@pytest.mark.parametrize("field", ["created_for", "scheduled_for", "published_at", "status", "history",
                                   "attempts"])
def test_removing_a_contract_field_breaks_the_schema(drafts, field):
    post = copylib.deepcopy(drafts.list()[0])
    del post[field]
    assert errors(post)


@pytest.mark.parametrize("change", [
    {"status": "scheduled"},  # uno stato nuovo richiede la v2 dello schema
    {"created_at": "2026-09-24T10:30:00+00:00"},  # le date sono UTC con Z, come now_iso
    {"scheduled_for": "2026-09-24T10:30:00.123Z"},
    {"created_for": "24/09/2026"},
    {"attempts": "1"},
])
def test_incompatible_values_break_the_schema(drafts, change):
    post = {**drafts.list()[0], **change}
    assert errors(post)


def test_status_specific_fields_are_required():
    assert errors({**_minimal(), "status": "published"})  # published_at ancora null
    assert errors({**_minimal(), "status": "approved"})  # senza approved_at/approved_by
    assert errors({**_minimal(), "status": "failed"})  # senza errore
    assert not errors({**_minimal(), "extra_field_added_later": 1})


def _minimal() -> dict:
    return {
        "id": "2026-09-24-who_is-it-tiktok", "status": "draft", "format": "who_is", "language": "it",
        "channel": "tiktok", "created_at": "2026-09-24T06:37:00Z", "created_for": "2026-09-24",
        "scheduled_for": "2026-09-24T10:00:00Z", "published_at": None, "external_url": None, "error": "",
        "attempts": 0, "history": [],
    }
