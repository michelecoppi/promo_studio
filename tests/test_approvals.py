import json

from helpers import NOW, settings

from promo import approvals, queue
from promo.store import MemoryStore

TOKEN = "987654:APPROVAL-secret-token"
ADMIN = "5550001"


class Response:
    def __init__(self, data):
        self._data, self.status_code = data, 200

    def json(self):
        return self._data


class TelegramFake:
    """Bot API finta: registra le chiamate e risponde con gli aggiornamenti in `pending`."""

    def __init__(self, pending=None):
        self.pending = list(pending or [])
        self.calls = []
        self.next_message_id = 100
        self.webhook = ""

    def post(self, url, **kwargs):
        method = url.rsplit("/", 1)[-1]
        data = kwargs.get("data") or {}
        self.calls.append((method, data))
        if method == "sendVideo":
            self.next_message_id += 1
            return Response({"ok": True, "result": {"message_id": self.next_message_id}})
        if method == "getWebhookInfo":
            return Response({"ok": True, "result": {"url": self.webhook}})
        if method == "setWebhook":
            self.webhook = data["url"]
        if method == "deleteWebhook":
            self.webhook = ""
        if method == "getUpdates":
            if "offset" in data:
                self.pending = [u for u in self.pending if u["update_id"] >= data["offset"]]
                return Response({"ok": True, "result": []})
            return Response({"ok": True, "result": list(self.pending)})
        return Response({"ok": True, "result": True})

    def sent(self, method):
        return [data for m, data in self.calls if m == method]


def draft(store, tmp_path, pid, channel, lang="it", sha="sha-a", scheduled="2026-09-24T10:00:00Z"):
    video = tmp_path / f"{sha}.mp4"
    video.write_bytes(b"video")
    store.create({
        "id": pid, "status": "draft", "channel": channel, "language": lang, "format": "who_is",
        "created_for": "2026-09-24", "caption": "Chi è?", "hashtags": ["#a", "#b", "#c"],
        "tracking_link": "https://t.me/bot?start=src_x", "media_path": str(video), "media_sha256": sha,
        "scheduled_for": scheduled, "history": [], "attempts": 0,
    })


def press(update_id, message_id, data, user_id=ADMIN, chat_id=ADMIN, username="michele"):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "data": data, "from": {"id": int(user_id), "username": username},
        "message": {"message_id": message_id, "chat": {"id": int(chat_id)}},
    }}


def morning(tmp_path):
    """Due video: italiano (TikTok + canale) ed inglese (solo TikTok). Prima delle 12:00."""
    store = MemoryStore()
    draft(store, tmp_path, "it-tiktok", "tiktok")
    draft(store, tmp_path, "it-telegram", "telegram_channel")
    draft(store, tmp_path, "en-tiktok", "tiktok", lang="en", sha="sha-b")
    return store


def make_settings(tmp_path, **kw):
    return settings(tmp_path, approval_bot_token=TOKEN, admin_chat_id=ADMIN, **kw)


def early():
    return NOW.replace(hour=6)  # 08:00 a Roma, prima della pubblicazione


def test_ask_sends_one_video_per_language_with_buttons_only_to_the_admin(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    bot = approvals.build_bot(make_settings(tmp_path), fake)
    lines = approvals.ask(store, bot, None, make_settings(tmp_path), now=early())

    videos = fake.sent("sendVideo")
    assert len(videos) == 2 and all(v["chat_id"] == ADMIN for v in videos)
    buttons = json.loads(videos[0]["reply_markup"])["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["ok", "no"]
    assert "canale Telegram, TikTok (bozza)" in videos[1]["caption"]  # "it" viene dopo "en"
    assert store.get("it-tiktok")["approval_message_id"] == store.get("it-telegram")["approval_message_id"]
    assert len(lines) == 2


def test_ask_does_not_send_twice_nor_expired_drafts(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    draft(store, tmp_path, "old", "tiktok", sha="sha-old", scheduled="2026-09-20T10:00:00Z")
    bot = approvals.build_bot(make_settings(tmp_path), fake)
    approvals.ask(store, bot, None, make_settings(tmp_path), now=early())
    assert approvals.ask(store, bot, None, make_settings(tmp_path), now=early()) == ["nessuna bozza da inviare"]
    assert len(fake.sent("sendVideo")) == 2
    assert not store.get("old").get("approval_message_id")


def test_sync_applies_admin_choices_to_every_channel_of_the_video(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    s = make_settings(tmp_path)
    approvals.ask(store, approvals.build_bot(s, fake), None, s, now=early())
    it_msg, en_msg = store.get("it-tiktok")["approval_message_id"], store.get("en-tiktok")["approval_message_id"]
    fake.pending = [press(1, it_msg, "ok"), press(2, en_msg, "no")]

    approvals.sync(store, approvals.build_bot(s, fake), s)

    assert store.get("it-tiktok")["status"] == "approved" and store.get("it-telegram")["status"] == "approved"
    assert store.get("it-tiktok")["approved_by"] == "michele"
    assert store.get("en-tiktok")["status"] == "rejected"
    edits = fake.sent("editMessageCaption")
    assert "✅ Approvato da michele" in edits[0]["caption"] and "❌ Rifiutato" in edits[1]["caption"]
    assert fake.pending == []  # confermati: Telegram non li ridara'
    assert fake.sent("getUpdates")[-1]["offset"] == 3


def test_sync_ignores_anyone_but_the_admin(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    s = make_settings(tmp_path)
    approvals.ask(store, approvals.build_bot(s, fake), None, s, now=early())
    msg = store.get("it-tiktok")["approval_message_id"]
    fake.pending = [press(1, msg, "ok", user_id="42", chat_id="42"),
                    press(2, msg, "ok", user_id="42", chat_id=ADMIN)]

    lines = approvals.sync(store, approvals.build_bot(s, fake), s)

    assert store.get("it-tiktok")["status"] == "draft"
    assert all("ignorato" in line for line in lines)


def test_sync_is_idempotent_and_does_not_undo_a_dashboard_decision(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    s = make_settings(tmp_path)
    approvals.ask(store, approvals.build_bot(s, fake), None, s, now=early())
    msg = store.get("it-tiktok")["approval_message_id"]
    queue.reject(store, "it-tiktok", "dashboard")
    fake.pending = [press(1, msg, "ok"), press(2, msg, "ok")]

    approvals.sync(store, approvals.build_bot(s, fake), s)

    assert store.get("it-tiktok")["status"] == "rejected"
    assert store.get("it-telegram")["status"] == "approved"
    assert len(fake.sent("editMessageCaption")) == 1


def test_sync_with_nothing_pressed(tmp_path):
    fake = TelegramFake()
    s = make_settings(tmp_path)
    assert approvals.sync(MemoryStore(), approvals.build_bot(s, fake), s) == ["nessuna risposta dall'admin"]


def test_no_bot_without_token_or_admin_chat(tmp_path):
    assert approvals.build_bot(settings(tmp_path, admin_chat_id=ADMIN)) is None
    assert approvals.build_bot(settings(tmp_path, approval_bot_token=TOKEN)) is None


def test_the_approval_token_is_a_secret(tmp_path):
    s = make_settings(tmp_path)
    assert TOKEN not in repr(s) and TOKEN in s.secret_values()


def test_publish_result_reaches_the_admin_only_when_something_happened(tmp_path):
    fake = TelegramFake()
    bot = approvals.build_bot(make_settings(tmp_path), fake)
    approvals.report_published(bot, ["niente da pubblicare"])
    approvals.report_published(bot, ["it-telegram: pubblicato (https://t.me/gtp/1)"])
    texts = fake.sent("sendMessage")
    assert len(texts) == 1 and "pubblicato" in texts[0]["text"] and texts[0]["chat_id"] == ADMIN


def test_sync_steps_aside_when_the_webhook_is_active(tmp_path):
    store, fake = morning(tmp_path), TelegramFake()
    s = make_settings(tmp_path)
    approvals.ask(store, approvals.build_bot(s, fake), None, s, now=early())
    fake.pending = [press(1, store.get("it-tiktok")["approval_message_id"], "ok")]
    fake.webhook = "https://promo-approvals.example.run.app/"

    lines = approvals.sync(store, approvals.build_bot(s, fake), s)

    assert "webhook attivo" in lines[0]
    assert store.get("it-tiktok")["status"] == "draft"
    assert not fake.sent("getUpdates")  # con il webhook Telegram lo rifiuterebbe


def test_webhook_is_set_with_the_secret_and_only_for_buttons(tmp_path):
    fake = TelegramFake()
    bot = approvals.build_bot(make_settings(tmp_path), fake)
    bot.set_webhook("https://promo-approvals.example.run.app/", "s3cret")
    sent = fake.sent("setWebhook")[0]
    assert sent["secret_token"] == "s3cret" and json.loads(sent["allowed_updates"]) == ["callback_query"]
    assert bot.webhook_url() == "https://promo-approvals.example.run.app/"
    bot.delete_webhook()
    assert bot.webhook_url() == ""
