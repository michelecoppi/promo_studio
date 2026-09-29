import io
import json

from test_approvals import ADMIN, Response, TelegramFake, early, make_settings, morning, press

from promo import approval_service, approvals
from promo.store import MemoryStore

SECRET = "webhook-s3cret"


def call(app, method="POST", body=None, secret=SECRET):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {"REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
    if secret is not None:
        environ[approval_service.SECRET_HEADER] = secret
    seen = {}

    def start_response(status, headers):
        seen["status"] = status

    b"".join(app(environ, start_response))
    return int(seen["status"].split()[0])


def service(tmp_path, secret=SECRET):
    """Il webhook su una coda con due video gia' mandati all'admin."""
    store, fake = morning(tmp_path), TelegramFake()
    s = make_settings(tmp_path, approval_webhook_secret=secret)
    approvals.ask(store, approvals.build_bot(s, fake), None, s, now=early())
    app = approval_service.make_app(s, lambda: store, lambda settings: approvals.build_bot(settings, fake))
    return app, store, fake


def test_a_press_is_applied_right_away(tmp_path):
    app, store, fake = service(tmp_path)
    msg = store.get("it-tiktok")["approval_message_id"]

    assert call(app, body=press(1, msg, "ok")) == 200

    assert store.get("it-tiktok")["status"] == "approved" and store.get("it-telegram")["status"] == "approved"
    assert "✅ Approvato da michele" in fake.sent("editMessageCaption")[0]["caption"]
    assert fake.sent("answerCallbackQuery")[0]["text"] == "Approvato"
    assert not fake.sent("getUpdates")


def test_reject_and_a_second_press_changes_nothing(tmp_path):
    app, store, fake = service(tmp_path)
    msg = store.get("en-tiktok")["approval_message_id"]

    assert call(app, body=press(1, msg, "no")) == 200
    assert call(app, body=press(2, msg, "ok")) == 200  # Telegram riprova, o doppio tocco

    assert store.get("en-tiktok")["status"] == "rejected"
    assert len(fake.sent("editMessageCaption")) == 1


def test_without_the_right_secret_nothing_happens(tmp_path):
    app, store, _ = service(tmp_path)
    msg = store.get("it-tiktok")["approval_message_id"]

    assert call(app, body=press(1, msg, "ok"), secret=None) == 403
    assert call(app, body=press(1, msg, "ok"), secret="sbagliato") == 403
    assert store.get("it-tiktok")["status"] == "draft"


def test_without_a_configured_secret_everything_is_refused(tmp_path):
    app, store, _ = service(tmp_path, secret="")
    msg = store.get("it-tiktok")["approval_message_id"]
    assert call(app, body=press(1, msg, "ok"), secret="") == 403
    assert store.get("it-tiktok")["status"] == "draft"


def test_only_the_admin_decides(tmp_path):
    app, store, _ = service(tmp_path)
    msg = store.get("it-tiktok")["approval_message_id"]
    assert call(app, body=press(1, msg, "ok", user_id="42", chat_id=ADMIN)) == 200
    assert store.get("it-tiktok")["status"] == "draft"


def test_health_check_bad_bodies_and_other_updates(tmp_path):
    app, store, _ = service(tmp_path)
    assert call(app, method="GET", secret=None) == 200
    assert call(app, method="PUT") == 405
    assert call(app, body=None) == 400
    assert call(app, body={"update_id": 1, "message": {"text": "/start"}}) == 200
    assert all(p["status"] == "draft" for p in store.list())


def test_a_failure_asks_telegram_to_retry(tmp_path):
    app, store, _ = service(tmp_path)
    msg = store.get("it-tiktok")["approval_message_id"]

    def broken():
        raise RuntimeError("Firestore non raggiungibile")

    store.list = broken
    assert call(app, body=press(1, msg, "ok")) == 500


def test_a_press_on_drafts_no_longer_in_queue_removes_the_buttons(tmp_path):
    app, store, fake = service(tmp_path)

    assert call(app, body=press(1, 999, "ok")) == 200

    assert fake.sent("answerCallbackQuery")[0]["text"] == "Post non trovato"
    closed = fake.sent("editMessageReplyMarkup")[0]
    assert closed["message_id"] == 999 and json.loads(closed["reply_markup"]) == {"inline_keyboard": []}
    assert "non sono più in coda" in fake.sent("sendMessage")[0]["text"]
    assert all(p["status"] == "draft" for p in store.list())


# --- /dispatch: Cloud Scheduler avvia i lavori su GitHub ------------------------------

INVOKER = "promo-scheduler@progetto.iam.gserviceaccount.com"
AUDIENCE = "https://promo-approvals.example.run.app"
GITHUB_TOKEN = "github_pat_finto"


class GitHubFake:
    def __init__(self, status=204):
        self.status, self.calls = status, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = Response({})
        response.status_code = self.status
        return response


def google_token(token, audience):
    """Verifica finta: 'buono' e' firmato da Google per l'invoker, il resto no."""
    if token == "buono" and audience == AUDIENCE:
        return {"email": INVOKER, "email_verified": True}
    if token == "altro-account" and audience == AUDIENCE:
        return {"email": "intruso@altro.iam.gserviceaccount.com", "email_verified": True}
    raise ValueError("token non valido")


def dispatcher(tmp_path, github=None, **kw):
    s = make_settings(tmp_path, github_dispatch_token=GITHUB_TOKEN, dispatch_invoker=INVOKER,
                      dispatch_audience=AUDIENCE, **kw)
    github = github or GitHubFake()
    app = approval_service.make_app(s, MemoryStore, lambda settings: None,
                                    verify_token=google_token, github_session=github)
    return app, github


def dispatch_call(app, command="drafts", token="buono", method="POST"):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": "/dispatch", "QUERY_STRING": f"command={command}",
               "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b"")}
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    seen = {}
    b"".join(app(environ, lambda status, headers: seen.setdefault("status", status)))
    return int(seen["status"].split()[0])


def test_scheduler_starts_the_workflow_for_real(tmp_path):
    app, github = dispatcher(tmp_path)

    assert dispatch_call(app, "publish") == 200

    url, kwargs = github.calls[0]
    assert url.endswith("/repos/michelecoppi/promo_studio/actions/workflows/promo.yml/dispatches")
    assert kwargs["json"] == {"ref": "main", "inputs": {"command": "publish", "dry_run": "false"}}
    assert kwargs["headers"]["Authorization"] == f"Bearer {GITHUB_TOKEN}"


def test_only_the_scheduler_identity_can_start_jobs(tmp_path):
    app, github = dispatcher(tmp_path)
    assert dispatch_call(app, token=None) == 403
    assert dispatch_call(app, token="falso") == 403
    assert dispatch_call(app, token="altro-account") == 403
    assert dispatch_call(app, method="GET") == 405
    assert github.calls == []


def test_without_invoker_or_audience_nobody_can_start_jobs(tmp_path):
    app, github = dispatcher(tmp_path, github=None)
    app = approval_service.make_app(make_settings(tmp_path, github_dispatch_token=GITHUB_TOKEN), MemoryStore,
                                    lambda settings: None, verify_token=google_token, github_session=github)
    assert dispatch_call(app) == 403 and github.calls == []


def test_only_known_commands_and_github_errors_ask_for_a_retry(tmp_path):
    app, github = dispatcher(tmp_path)
    assert dispatch_call(app, "sync") == 400
    assert dispatch_call(app, "drafts%20--dry-run") == 400
    assert github.calls == []

    app, _ = dispatcher(tmp_path, github=GitHubFake(status=401))
    assert dispatch_call(app) == 502


def test_the_github_token_is_a_secret(tmp_path):
    s = make_settings(tmp_path, github_dispatch_token=GITHUB_TOKEN)
    assert GITHUB_TOKEN not in repr(s) and GITHUB_TOKEN in s.secret_values()
