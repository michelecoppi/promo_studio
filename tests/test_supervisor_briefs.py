"""Brief del supervisore dal suo Firestore, con ✅ Usa / ❌ Scarta sul bot approvazioni (#7).

Niente rete e niente Firestore: il supervisore e' una fonte statica (o un client finto), il bot e'
`TelegramFake`, il webhook e' l'app WSGI chiamata a mano."""
import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fakes import FakeGame
from helpers import fake_render
from test_approval_service import SECRET, call
from test_approvals import ADMIN, TelegramFake, make_settings, press

from promo import approval_service, approvals, cli, render, supervisor_briefs
from promo.store import MemoryStore

# Lo stesso file di gtp_orchestrator (tests/fixtures/promo_brief_doc.json): se il supervisore cambia
# il formato, si aggiornano entrambi.
FIXTURE = Path(__file__).parent / "fixtures" / "supervisor_promo_brief.json"
SUP_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
CID = SUP_DOC["campaign_id"]
NOW = datetime(2026, 9, 29, 6, 40, tzinfo=timezone.utc)  # 08:40 a Roma, dopo le bozze


def source(*docs):
    return supervisor_briefs.StaticBriefSource([copy.deepcopy(d) for d in (docs or (SUP_DOC,))])


def asked(tmp_path, fake=None):
    """Brief della fixture gia' proposto all'admin: (decisioni, fake, settings)."""
    fake = fake or TelegramFake()
    s = make_settings(tmp_path, approval_webhook_secret=SECRET)
    decisions = MemoryStore()
    supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, fake), now=NOW)
    return decisions, fake, s


def webhook(tmp_path):
    decisions, fake, s = asked(tmp_path)
    app = approval_service.make_app(s, MemoryStore, lambda settings: approvals.build_bot(settings, fake),
                                    decisions_factory=lambda store: decisions)
    return app, decisions, fake


# --- lettura -------------------------------------------------------------------------------------
def test_the_supervisor_document_is_a_valid_brief_import_payload():
    ready, lines = supervisor_briefs.pending(source(), NOW)
    assert lines == [] and [r["brief"]["campaign_id"] for r in ready] == [CID]
    brief = ready[0]["brief"]
    assert (brief["format"], brief["language"], brief["channel"]) == ("who_is", "it", "tiktok")
    assert brief["facts"] == SUP_DOC["brief"]["facts"]


def test_expired_unknown_or_invalid_briefs_are_not_proposed():
    expired = {**SUP_DOC, "campaign_id": "old", "brief": {**SUP_DOC["brief"], "campaign_id": "old"},
               "expires_at": "2026-09-01T00:00:00Z"}
    future = {**SUP_DOC, "campaign_id": "v2", "schema_version": 2}
    bad = {**SUP_DOC, "campaign_id": "bad", "brief": {**SUP_DOC["brief"], "campaign_id": "bad", "format": "boh"}}
    decided = {**SUP_DOC, "campaign_id": "gone", "status": "withdrawn"}
    ready, lines = supervisor_briefs.pending(source(expired, future, bad, decided), NOW)
    assert ready == []
    assert any("schema_version 2" in line for line in lines) and any("bad: brief non valido" in line for line in lines)


def test_firestore_source_only_reads_proposed_documents():
    class Query:
        def __init__(self, log):
            self.log = log

        def where(self, *args):
            self.log.append(("where", args))
            return self

        def stream(self):
            self.log.append(("stream",))
            doc = type("Doc", (), {"to_dict": lambda self: dict(SUP_DOC)})()
            return [doc]

    class Client:
        def __init__(self):
            self.log = []

        def collection(self, name):
            self.log.append(("collection", name))
            return Query(self.log)

    client = Client()
    docs = supervisor_briefs.FirestoreBriefSource("gtp-orchestrator", client).proposed()
    assert docs == [SUP_DOC]
    assert client.log == [("collection", "promo_briefs"), ("where", ("status", "==", "proposed")), ("stream",)]


# --- proposta all'admin ----------------------------------------------------------------------------
def test_ask_sends_each_brief_once_with_use_and_discard_buttons(tmp_path):
    decisions, fake, s = asked(tmp_path)
    (sent,) = fake.sent("sendMessage")
    assert sent["chat_id"] == ADMIN and "parse_mode" not in sent  # testo del modello: niente HTML
    assert CID in sent["text"] and "CTA: Gioca la sfida di oggi" in sent["text"]
    assert "Angolo: Riconosci il campione dal percorso" in sent["text"] and "• Ogni giorno" in sent["text"]
    buttons = json.loads(sent["reply_markup"])["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["✅ Usa", "❌ Scarta"]
    assert [b["callback_data"] for b in buttons] == [f"brief:use:{CID}", f"brief:skip:{CID}"]
    decision = decisions.get(CID)
    assert decision["status"] == "asked" and decision["message_id"] == 101 and decision["brief"]["format"] == "who_is"
    # Rilanciato (secondo cron, giorno dopo): nessun doppione.
    lines = supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, fake), now=NOW)
    assert lines == ["nessun brief nuovo del supervisore"] and len(fake.sent("sendMessage")) == 1


def test_a_failed_send_is_retried_but_an_interrupted_one_is_not(tmp_path):
    class Broken(TelegramFake):
        def post(self, url, **kwargs):
            if url.endswith("/sendMessage"):
                raise RuntimeError("rete giu'")
            return super().post(url, **kwargs)

    s = make_settings(tmp_path)
    decisions = MemoryStore()
    lines = supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, Broken()), now=NOW)
    assert "si ritenta" in lines[0] and decisions.get(CID)["status"] == "send_failed"
    fake = TelegramFake()
    supervisor_briefs.ask(source(), decisions, approvals.build_bot(s, fake), now=NOW)
    assert decisions.get(CID)["status"] == "asked" and len(fake.sent("sendMessage")) == 1
    # Crash fra creazione e invio: `asking` resta cosi', non si manda alla cieca un secondo messaggio.
    stuck = MemoryStore({CID: {"id": CID, "campaign_id": CID, "status": "asking"}})
    supervisor_briefs.ask(source(), stuck, approvals.build_bot(s, fake), now=NOW)
    assert len(fake.sent("sendMessage")) == 1


def test_dry_run_neither_writes_nor_sends(tmp_path):
    decisions = MemoryStore()
    lines = supervisor_briefs.ask(source(), decisions, None, dry_run=True, now=NOW)
    assert lines == [f"{CID}: (dry-run) da proporre all'admin"] and decisions.list() == []


# --- pulsanti ----------------------------------------------------------------------------------------
def test_use_is_recorded_once_and_a_second_press_changes_nothing(tmp_path):
    app, decisions, fake = webhook(tmp_path)
    msg = decisions.get(CID)["message_id"]

    assert call(app, body=press(1, msg, f"brief:use:{CID}")) == 200
    assert call(app, body=press(2, msg, f"brief:skip:{CID}")) == 200  # doppio tocco o riprova di Telegram

    decision = decisions.get(CID)
    assert decision["status"] == "used" and decision["decided_by"] == "michele" and decision["decided_at"]
    assert decision["message_id"] == msg
    (edited,) = fake.sent("editMessageText")
    assert "✅ Usato da michele" in edited["text"] and "reply_markup" not in edited  # pulsanti tolti
    assert [a["text"] for a in fake.sent("answerCallbackQuery")] == ["Brief usato", "Gia' deciso: usato"]


def test_discard_is_final(tmp_path):
    app, decisions, fake = webhook(tmp_path)
    msg = decisions.get(CID)["message_id"]
    assert call(app, body=press(1, msg, f"brief:skip:{CID}")) == 200
    assert decisions.get(CID)["status"] == "discarded"
    assert "❌ Scartato da michele" in fake.sent("editMessageText")[0]["text"]
    # Il supervisore lo ha ancora `proposed`: non torna all'admin.
    assert supervisor_briefs.ask(source(), decisions, approvals.build_bot(make_settings(tmp_path), fake),
                                 now=NOW) == ["nessun brief nuovo del supervisore"]


def test_only_the_admin_in_his_chat_decides_a_brief(tmp_path):
    app, decisions, fake = webhook(tmp_path)
    msg = decisions.get(CID)["message_id"]
    assert call(app, body=press(1, msg, f"brief:use:{CID}", user_id="42")) == 200
    assert call(app, body=press(2, msg, f"brief:use:{CID}", chat_id="-100123")) == 200
    assert call(app, body=press(3, msg, f"brief:use:{CID}"), secret="sbagliato") == 403
    assert decisions.get(CID)["status"] == "asked" and not fake.sent("answerCallbackQuery")


def test_brief_buttons_never_touch_posts_and_unknown_briefs_are_answered(tmp_path):
    app, decisions, fake = webhook(tmp_path)
    msg = decisions.get(CID)["message_id"]
    assert call(app, body=press(1, msg, "brief:use:altro")) == 200
    assert call(app, body=press(2, msg, "brief:boh:x")) == 200
    assert [a["text"] for a in fake.sent("answerCallbackQuery")] == ["Brief non trovato", "Pulsante non valido"]
    assert not fake.sent("editMessageReplyMarkup")  # non e' trattato come un post sparito
    assert decisions.get(CID)["status"] == "asked"


def test_sync_routes_brief_buttons_too(tmp_path):
    decisions, fake, s = asked(tmp_path)
    fake.pending = [press(1, decisions.get(CID)["message_id"], f"brief:skip:{CID}")]
    lines = approvals.sync(MemoryStore(), approvals.build_bot(s, fake), s, decisions)
    assert lines == [f"{CID}: discarded da michele (Telegram)"]


def test_without_decisions_store_the_press_is_answered_and_ignored(tmp_path):
    s = make_settings(tmp_path, approval_webhook_secret=SECRET)
    fake = TelegramFake()
    app = approval_service.make_app(s, MemoryStore, lambda settings: approvals.build_bot(settings, fake))
    assert call(app, body=press(1, 7, f"brief:use:{CID}")) == 200
    assert fake.sent("answerCallbackQuery")[0]["text"] == "Brief non gestiti qui"


# --- dai brief usati alle bozze ----------------------------------------------------------------------
def test_used_briefs_become_drafts_once_and_discarded_ones_never(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    decisions, fake, s = asked(tmp_path)
    decisions.update(CID, {"status": "used", "decided_by": "michele"})
    other = {**decisions.get(CID), "id": "w40-no", "campaign_id": "w40-no", "status": "discarded",
             "brief": {**decisions.get(CID)["brief"], "campaign_id": "w40-no"}}
    decisions.create(other)
    store = MemoryStore()

    supervisor_briefs.apply_used(s, FakeGame(), store, None, decisions, now=NOW)
    (post,) = store.list()
    assert post["status"] == "draft" and post["brief_id"] == CID and post["format"] == "who_is"
    assert post["brief"]["cta"] == "Gioca la sfida di oggi"
    decision = decisions.get(CID)
    assert decision["imported_at"] and decision["imported_for"] == FakeGame().today()
    # Secondo giro (o giorno dopo): gia' importato, nessuna bozza nuova.
    lines = supervisor_briefs.apply_used(s, FakeGame(), store, None, decisions, now=NOW)
    assert lines == ["nessun brief del supervisore da trasformare in bozze"] and len(store.list()) == 1


def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    decisions, _, s = asked(tmp_path)
    decisions.update(CID, {"status": "used"})
    store = MemoryStore()
    lines = supervisor_briefs.apply_used(s, FakeGame(), store, None, decisions, dry_run=True, now=NOW)
    assert "(dry-run)" in lines[0] and store.list() == [] and not decisions.get(CID).get("imported_at")


# --- configurazione e confini -------------------------------------------------------------------------
def test_feature_is_off_without_the_supervisor_project(monkeypatch, capsys):
    monkeypatch.delenv("PROMO_SUPERVISOR_FIRESTORE_PROJECT", raising=False)
    monkeypatch.setattr("promo.cli.load", lambda: make_settings(Path(".")))
    assert cli.main(["brief-ask"]) == 0 and cli.main(["brief-apply"]) == 0
    assert capsys.readouterr().out.count("brief del supervisore spenti") == 2


def test_decisions_live_next_to_the_queue(tmp_path):
    from promo.store import FirestoreStore, JsonFileStore

    firestore = supervisor_briefs.decisions_for(FirestoreStore(db="db"))
    assert firestore.collection == "promo_brief_decisions" and firestore.db == "db"
    local = supervisor_briefs.decisions_for(JsonFileStore(tmp_path / "promo_posts.json"))
    assert local.path == tmp_path / "promo_brief_decisions.json"
    assert supervisor_briefs.decisions_for(MemoryStore()) is None


def test_the_webhook_still_imports_without_rendering_dependencies():
    """Il servizio su Cloud Run installa solo requirements-approvals.txt: niente Pillow."""
    code = ("import sys; import promo.approval_service, promo.supervisor_briefs; "
            "sys.exit(1 if 'PIL' in sys.modules or 'promo.briefs' in sys.modules else 0)")
    root = Path(__file__).resolve().parents[1]
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0
