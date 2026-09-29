"""Il contratto di `promo_brief_decisions` (docs/schemas/promo_brief_decision.v1.json) contro i documenti veri.

Il supervisore (michelecoppi/gtp_orchestrator, collector Promo) legge le decisioni sui suoi brief in sola
lettura e valida le sue fixture contro una copia di questo schema. Qui si controlla l'altro lato: ogni
documento che `supervisor_briefs` scrive (proposta, invio fallito e ritento, pulsanti ✅ Usa / ❌ Scarta,
bozze create o non create) rispetta lo schema. Se un cambio al codice rompe questi test, si aggiorna lo
schema (una v2 se il cambio e' incompatibile) e si apre un'issue su gtp_orchestrator.
"""
import json
from pathlib import Path

import pytest
from fakes import FakeGame
from helpers import fake_render
from jsonschema import Draft202012Validator
from test_approval_service import call
from test_approvals import TelegramFake, make_settings, press
from test_supervisor_briefs import CID, NOW, asked, source, webhook

from promo import approvals, briefs, config, models, render, supervisor_briefs
from promo.store import MemoryStore

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "promo_brief_decision.v1.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
# Campi che il supervisore legge (collectors/promo.py::DECISION_FIELDS): devono restare nello schema.
READ_BY_SUPERVISOR = ("campaign_id", "status", "asked_at", "decided_at", "imported_for")


def errors(doc: dict) -> list:
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in VALIDATOR.iter_errors(doc)]


def assert_valid(doc: dict) -> None:
    assert errors(doc) == [], doc


class Broken(TelegramFake):
    def post(self, url, **kwargs):
        if url.endswith("/sendMessage"):
            raise RuntimeError("rete giu'")
        return super().post(url, **kwargs)


def test_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["additionalProperties"] is True  # Promo puo' aggiungere campi senza una v2


def test_schema_enums_match_the_code():
    defs = SCHEMA["$defs"]
    sb = supervisor_briefs
    assert set(defs["status"]["enum"]) == {sb.ASKING, sb.ASKED, sb.SEND_FAILED, sb.USED, sb.DISCARDED}
    brief = defs["brief"]["properties"]
    assert tuple(brief["language"]["enum"]) == config.LANGUAGES
    assert tuple(brief["format"]["enum"]) == briefs.BRIEF_FORMATS
    assert tuple(brief["channel"]["enum"]) == models.CHANNELS
    assert defs["campaign_id"]["pattern"] == briefs._CAMPAIGN_ID.pattern
    assert set(READ_BY_SUPERVISOR) <= set(SCHEMA["properties"])


def test_asked_decision_is_valid(tmp_path):
    decisions, _, _ = asked(tmp_path)
    decision = decisions.get(CID)
    assert decision["status"] == "asked" and decision["id"] == decision["campaign_id"] == CID
    assert_valid(decision)


def test_failed_send_and_retry_are_valid(tmp_path):
    s = make_settings(tmp_path)
    decisions = MemoryStore()
    supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, Broken()), now=NOW)
    failed = decisions.get(CID)
    assert failed["status"] == "send_failed" and failed["message_id"] is None and failed["error"]
    assert_valid(failed)
    supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, TelegramFake()), now=NOW)
    retried = decisions.get(CID)
    assert retried["status"] == "asked"
    assert_valid(retried)


def test_asking_left_by_a_crash_is_valid(tmp_path):
    """Lo stato fra la creazione e l'invio, come lo scrive `ask` prima di chiamare Telegram."""
    class Crash(TelegramFake):
        def post(self, url, **kwargs):
            if url.endswith("/sendMessage"):
                raise KeyboardInterrupt  # il processo muore durante l'invio
            return super().post(url, **kwargs)

    decisions = MemoryStore()
    with pytest.raises(KeyboardInterrupt):
        supervisor_briefs.ask(source(), decisions, approvals.build_bot(make_settings(tmp_path), Crash()), now=NOW)
    stuck = decisions.get(CID)
    assert stuck["status"] == "asking" and stuck["message_id"] is None
    assert_valid(stuck)


@pytest.mark.parametrize("choice, status", [("use", "used"), ("skip", "discarded")])
def test_pressed_decisions_are_valid(tmp_path, choice, status):
    app, decisions, _ = webhook(tmp_path)
    assert call(app, body=press(1, decisions.get(CID)["message_id"], f"brief:{choice}:{CID}")) == 200
    decision = decisions.get(CID)
    assert decision["status"] == status and decision["decided_by"] == "michele"
    assert_valid(decision)


def test_imported_and_failed_import_are_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    app, decisions, _ = webhook(tmp_path)
    call(app, body=press(1, decisions.get(CID)["message_id"], f"brief:use:{CID}"))
    s = make_settings(tmp_path)

    def unreachable(*args, **kwargs):
        raise RuntimeError("gioco non raggiungibile")

    broken = MemoryStore(decisions.posts)
    with monkeypatch.context() as m:
        m.setattr(briefs, "import_brief", unreachable)
        supervisor_briefs.apply_used(s, FakeGame(), MemoryStore(), None, broken, now=NOW)
    not_imported = broken.get(CID)
    assert not_imported["import_error"] and "imported_at" not in not_imported
    assert_valid(not_imported)

    supervisor_briefs.apply_used(s, FakeGame(), MemoryStore(), None, decisions, now=NOW)
    imported = decisions.get(CID)
    assert imported["imported_at"] and imported["imported_for"] == FakeGame().today()
    assert imported["import_error"] is None
    assert_valid(imported)


@pytest.mark.parametrize("field", ["campaign_id", "status", "asked_at", "brief", "message_id"])
def test_removing_a_contract_field_breaks_the_schema(tmp_path, field):
    decisions, _, _ = asked(tmp_path)
    doc = decisions.get(CID)
    del doc[field]
    assert errors(doc)


@pytest.mark.parametrize("change", [
    {"status": "withdrawn"},  # uno stato nuovo richiede la v2 dello schema
    {"asked_at": "2026-09-29T06:40:00+00:00"},  # le date sono UTC con Z, come now_iso
    {"campaign_id": "2026W40_WhoIs"},
    {"message_id": "101"},
    {"status": "used"},  # senza decided_by / decided_at
    {"status": "send_failed"},  # senza errore
    {"imported_at": "2026-09-29T06:40:00Z"},  # senza imported_for, e su un brief non usato
    {"imported_for": "29/09/2026"},
])
def test_incompatible_values_break_the_schema(tmp_path, change):
    decisions, _, _ = asked(tmp_path)
    assert errors({**decisions.get(CID), **change})
    assert not errors({**decisions.get(CID), "extra_field_added_later": 1})
