import io
import json

from test_approvals import ADMIN, TelegramFake, early, make_settings, morning, press

from promo import approval_service, approvals

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
