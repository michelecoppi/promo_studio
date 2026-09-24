import pytest
from fakes import FakeGame
from helpers import NOW, fake_render, settings

from promo import plan, queue, render
from promo.publishers.base import Publisher, PublishResult
from promo.publishers.telegram import TelegramChannelPublisher
from promo.publishers.tiktok import FileTokenStore, TikTokDraftPublisher, TokenStore, chunk_plan
from promo.store import MemoryStore

TOKEN = "123456:SECRET-token-value"


class Response:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data, status

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # (method, substring) -> Response | callable | list
        self.calls = []

    def _handle(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for (m, part), value in self.routes.items():
            if m == method and part in url:
                if isinstance(value, list):
                    value = value.pop(0) if len(value) > 1 else value[0]
                if callable(value):
                    return value(url, kwargs)
                return value
        raise AssertionError(f"chiamata inattesa {method} {url}")

    def post(self, url, **kwargs):
        return self._handle("POST", url, kwargs)

    def put(self, url, **kwargs):
        return self._handle("PUT", url, kwargs)


def telegram_session(chat_type="channel"):
    return FakeSession({
        ("POST", "/getChat"): Response({"ok": True, "result": {"id": -1001234, "type": chat_type, "username": "gtp_promo"}}),
        ("POST", "/sendVideo"): Response({"ok": True, "result": {"message_id": 42}}),
    })


def approved_post(store, tmp_path, pid="2026-09-10-who_is-it-telegram_channel", channel="telegram_channel"):
    video = tmp_path / f"{pid}.mp4"
    video.write_bytes(b"x" * 1000)
    store.create({
        "id": pid, "status": "draft", "channel": channel, "caption": "Chi è?", "hashtags": ["#a", "#b", "#c"],
        "tracking_link": "https://t.me/guess_the_player_from_path_bot?start=src_x", "media_path": str(video),
        "scheduled_for": "2026-09-24T10:00:00Z", "history": [], "attempts": 0, "duration": 20.0,
    })
    queue.approve(store, pid, "michele")
    return pid


# --- Telegram -----------------------------------------------------------------------------

def test_telegram_publishes_once_and_saves_external_id(tmp_path):
    store, session = MemoryStore(), telegram_session()
    pid = approved_post(store, tmp_path)
    publishers = {"telegram_channel": TelegramChannelPublisher(TOKEN, "@gtp_promo", session)}
    plan.publish_due(store, publishers, None, settings(tmp_path), now=NOW)
    post = store.get(pid)
    assert post["status"] == "published"
    assert post["external_id"] == "@gtp_promo:42" and post["external_url"] == "https://t.me/gtp_promo/42"
    sent = [c for c in session.calls if c[1].endswith("/sendVideo")]
    assert len(sent) == 1 and sent[0][2]["data"]["chat_id"] == "@gtp_promo"
    assert "src_x" in sent[0][2]["data"]["caption"]
    plan.publish_due(store, publishers, None, settings(tmp_path), now=NOW)
    assert len([c for c in session.calls if c[1].endswith("/sendVideo")]) == 1  # idempotente


def test_telegram_refuses_groups(tmp_path):
    store, session = MemoryStore(), telegram_session(chat_type="supergroup")
    pid = approved_post(store, tmp_path)
    plan.publish_due(store, {"telegram_channel": TelegramChannelPublisher(TOKEN, "-100999", session)}, None,
                     settings(tmp_path), now=NOW)
    post = store.get(pid)
    assert post["status"] == "failed" and "canale" in post["error"]
    assert not [c for c in session.calls if c[1].endswith("/sendVideo")]


def test_errors_never_leak_the_token(tmp_path):
    def boom(url, kwargs):
        raise __import__("requests").ConnectionError(f"cannot reach {url}")
    store = MemoryStore()
    pid = approved_post(store, tmp_path)
    session = FakeSession({("POST", "/getChat"): boom})
    plan.publish_due(store, {"telegram_channel": TelegramChannelPublisher(TOKEN, "@c", session)}, None,
                     settings(tmp_path), now=NOW)
    post = store.get(pid)
    assert post["status"] == "failed" and TOKEN not in post["error"] and "SECRET" not in str(post["history"])


def test_draft_and_future_posts_are_not_published(tmp_path):
    store, session = MemoryStore(), telegram_session()
    store.create({"id": "d", "status": "draft", "channel": "telegram_channel", "scheduled_for": "2026-09-01T00:00:00Z",
                  "history": []})
    pid = approved_post(store, tmp_path)
    store.update(pid, {"scheduled_for": "2026-09-25T10:00:00Z"})
    lines = plan.publish_due(store, {"telegram_channel": TelegramChannelPublisher(TOKEN, "@c", session)}, None,
                             settings(tmp_path), now=NOW)
    assert lines == ["niente da pubblicare"] and not session.calls


def test_dry_run_makes_no_external_calls_and_no_changes(tmp_path):
    store, session = MemoryStore(), telegram_session()
    pid = approved_post(store, tmp_path)
    before = store.get(pid)
    lines = plan.publish_due(store, {"telegram_channel": TelegramChannelPublisher(TOKEN, "@c", session)}, None,
                             settings(tmp_path), dry_run=True, now=NOW)
    assert "(dry-run)" in lines[0] and not session.calls and store.get(pid) == before


class Exploding(Publisher):
    channel = "telegram_channel"

    def publish(self, post, media_path):
        raise RuntimeError("kaboom")


class Counting(Publisher):
    channel = "tiktok"

    def __init__(self):
        self.count = 0

    def publish(self, post, media_path):
        self.count += 1
        return PublishResult(ok=True, external_id=f"id{self.count}")


def test_one_failure_does_not_stop_the_batch_and_failed_is_retried(tmp_path):
    store = MemoryStore()
    bad = approved_post(store, tmp_path, "a-who_is-it-telegram_channel")
    good = approved_post(store, tmp_path, "b-who_is-it-tiktok", channel="tiktok")
    counting = Counting()
    plan.publish_due(store, {"telegram_channel": Exploding(), "tiktok": counting}, None, settings(tmp_path), now=NOW)
    assert store.get(bad)["status"] == "failed" and "kaboom" in store.get(bad)["error"]
    assert store.get(good)["status"] == "published" and counting.count == 1
    # riprova: failed → published
    plan.publish_due(store, {"telegram_channel": Counting(), "tiktok": counting}, None, settings(tmp_path), now=NOW)
    assert store.get(bad)["status"] == "published" and counting.count == 1


def test_failed_gives_up_after_max_attempts(tmp_path):
    store = MemoryStore()
    pid = approved_post(store, tmp_path)
    for _ in range(plan.MAX_ATTEMPTS + 2):
        plan.publish_due(store, {"telegram_channel": Exploding()}, None, settings(tmp_path), now=NOW)
    assert store.get(pid)["attempts"] == plan.MAX_ATTEMPTS


def test_concurrent_claim_publishes_once(tmp_path):
    store = MemoryStore()
    pid = approved_post(store, tmp_path)
    store.update(pid, {"publishing_since": "2026-09-24T10:29:00Z"})  # un'altra esecuzione l'ha preso
    counting = Counting()
    counting.channel = "telegram_channel"
    lines = plan.publish_due(store, {"telegram_channel": counting}, None, settings(tmp_path), now=NOW)
    assert counting.count == 0 and "saltato" in lines[0]


def test_missing_media_is_regenerated_from_render_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    from promo import picker
    card = picker.pick(FakeGame(), "who_is", "it", seed="s")
    store = MemoryStore()
    pid = approved_post(store, tmp_path)
    store.update(pid, {"media_path": str(tmp_path / "gone.mp4"), "created_for": "2026-09-24",
                       "render_spec": {"format": "who_is", "language": "it", "cards": [card.to_dict()], "basename": "b"}})
    counting = Counting()
    counting.channel = "telegram_channel"
    seen = []
    counting.publish = lambda post, media: (seen.append(media), PublishResult(ok=True, external_id="1"))[1]
    plan.publish_due(store, {"telegram_channel": counting}, None, settings(tmp_path), now=NOW)
    assert seen and seen[0].endswith("b.mp4") and store.get(pid)["status"] == "published"


# --- TikTok -------------------------------------------------------------------------------

def tiktok_session(status_sequence=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"), new_refresh="r2"):
    return FakeSession({
        ("POST", "/oauth/token/"): Response({"access_token": "acc-123456", "refresh_token": new_refresh, "expires_in": 86400}),
        ("POST", "/inbox/video/init/"): Response({"data": {"publish_id": "v_inbox_1", "upload_url": "https://upload.example/u"},
                                                   "error": {"code": "ok"}}),
        ("PUT", "upload.example"): Response({}, 201),
        ("POST", "/status/fetch/"): [Response({"data": {"status": s}, "error": {"code": "ok"}}) for s in status_sequence],
    })


def test_tiktok_uploads_as_draft_and_rotates_token(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 2048)
    tokens = FileTokenStore(tmp_path / "tok.json", "r1")
    session = tiktok_session()
    publisher = TikTokDraftPublisher("key", "secret", tokens, session, sleep=lambda s: None)
    result = publisher.publish({"id": "p"}, str(video))
    assert result.ok and result.external_id == "v_inbox_1"
    init = next(c for c in session.calls if "init" in c[1])
    assert init[2]["json"]["source_info"] == {"source": "FILE_UPLOAD", "video_size": 2048, "chunk_size": 2048,
                                              "total_chunk_count": 1}
    assert init[2]["headers"]["Authorization"] == "Bearer acc-123456"
    put = next(c for c in session.calls if c[0] == "PUT")
    assert put[2]["headers"]["Content-Range"] == "bytes 0-2047/2048"
    assert tokens.load() == "r2" and oct((tmp_path / "tok.json").stat().st_mode)[-3:] == "600"


def test_tiktok_failure_is_reported():
    session = tiktok_session(status_sequence=("FAILED",))
    publisher = TikTokDraftPublisher("key", "secret", TokenStore("r1"), session, sleep=lambda s: None)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
        fh.write(b"x" * 10)
        fh.flush()
        result = publisher.publish({"id": "p"}, fh.name)
    assert not result.ok and "rifiutato" in result.error


def test_tiktok_without_credentials_fails_cleanly(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = TikTokDraftPublisher("", "", TokenStore(""), FakeSession({})).publish({"id": "p"}, str(video))
    assert not result.ok and "non configurati" in result.error


def test_chunk_plan():
    assert chunk_plan(1000) == (1000, 1)
    assert chunk_plan(64 * 1024 * 1024) == (64 * 1024 * 1024, 1)
    size = 100 * 1024 * 1024
    chunk, count = chunk_plan(size)
    assert count == size // chunk and size - (count - 1) * chunk <= 2 * 64 * 1024 * 1024
    with pytest.raises(ValueError):
        chunk_plan(0)


def test_ignore_schedule_only_for_a_single_post(tmp_path):
    store, session = MemoryStore(), telegram_session()
    pid = approved_post(store, tmp_path)
    store.update(pid, {"scheduled_for": "2099-01-01T10:00:00Z"})
    publishers = {"telegram_channel": TelegramChannelPublisher(TOKEN, "@c", session)}
    with pytest.raises(ValueError):
        plan.publish_due(store, publishers, None, settings(tmp_path), ignore_schedule=True, now=NOW)
    assert plan.publish_due(store, publishers, None, settings(tmp_path), now=NOW) == ["niente da pubblicare"]
    plan.publish_due(store, publishers, None, settings(tmp_path), only_id=pid, ignore_schedule=True, now=NOW)
    assert store.get(pid)["status"] == "published"
