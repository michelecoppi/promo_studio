from urllib.parse import parse_qs, urlparse

import pytest

from promo import envfile, tiktok_auth
from promo.publishers.tiktok import EnvFileTokenStore, TikTokDraftPublisher, TikTokError, token_store

REDIRECT = "https://game.example/privacy"


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class Session:
    def __init__(self, token, user=None):
        self.token, self.user, self.calls = token, user, []

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.token

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.user or Resp({}, 500)


def test_authorize_url_asks_upload_scope():
    query = parse_qs(urlparse(tiktok_auth.authorize_url("ck", REDIRECT, "st")).query)
    assert query["client_key"] == ["ck"] and query["redirect_uri"] == [REDIRECT] and query["state"] == ["st"]
    assert "video.upload" in query["scope"][0] and query["response_type"] == ["code"]


def test_parse_callback():
    assert tiktok_auth.parse_callback(f"{REDIRECT}?code=abc%2A1&scopes=x&state=st", "st") == "abc*1"
    assert tiktok_auth.parse_callback("  abc  ", "st") == "abc"
    with pytest.raises(TikTokError, match="state"):
        tiktok_auth.parse_callback(f"{REDIRECT}?code=abc&state=altro", "st")
    with pytest.raises(TikTokError, match="negata"):
        tiktok_auth.parse_callback(f"{REDIRECT}?error=access_denied&error_description=no&state=st", "st")
    with pytest.raises(TikTokError, match="code"):
        tiktok_auth.parse_callback(REDIRECT, "st")


def test_connect_exchanges_code_and_reads_name():
    session = Session(Resp({"access_token": "a", "refresh_token": "r", "scope": "user.info.basic,video.upload"}),
                      Resp({"data": {"user": {"display_name": "Guess the Player"}}}))
    result = tiktok_auth.connect("ck", "cs", REDIRECT, "code1", session)
    assert result == {"refresh_token": "r", "display_name": "Guess the Player", "scope": "user.info.basic,video.upload"}
    data = session.calls[0][2]["data"]
    assert data["grant_type"] == "authorization_code" and data["code"] == "code1" and data["redirect_uri"] == REDIRECT


def test_connect_requires_upload_scope_and_tokens():
    with pytest.raises(TikTokError, match="video.upload"):
        tiktok_auth.connect("ck", "cs", REDIRECT, "c", Session(Resp({"access_token": "a", "refresh_token": "r",
                                                                        "scope": "user.info.basic"})))
    with pytest.raises(TikTokError, match="invalid_grant"):
        tiktok_auth.connect("ck", "cs", REDIRECT, "c", Session(Resp({"error": "invalid_grant"}, 400)))


def test_connect_works_without_user_info():
    session = Session(Resp({"access_token": "a", "refresh_token": "r", "scope": "video.upload"}))
    assert tiktok_auth.connect("ck", "cs", REDIRECT, "c", session)["display_name"] == ""


def test_rotated_refresh_token_is_written_back_to_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TIKTOK_REFRESH_TOKEN=r1\n", encoding="utf-8")
    monkeypatch.delenv("PROMO_TIKTOK_TOKEN_FILE", raising=False)
    from helpers import settings
    store = token_store(settings(tmp_path, tiktok_refresh_token="r1"), env)
    assert isinstance(store, EnvFileTokenStore)
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "r1")
    publisher = TikTokDraftPublisher("ck", "cs", store,
                                     Session(Resp({"access_token": "a", "refresh_token": "r2"})))
    assert publisher.access_token() == "a"
    assert envfile.read(env)["TIKTOK_REFRESH_TOKEN"] == "r2"


def test_without_env_file_token_stays_in_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("PROMO_TIKTOK_TOKEN_FILE", raising=False)
    from helpers import settings
    assert type(token_store(settings(tmp_path), tmp_path / "missing.env")).__name__ == "TokenStore"
