import json

import pytest
from fakes import FakeGame
from helpers import fake_render

from promo import cli, game, render


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("PROMO_ENABLED", "BOT_TOKEN", "PROMO_TELEGRAM_CHANNEL_ID", "TIKTOK_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROMO_STORE", "local")
    monkeypatch.setenv("PROMO_LOCAL_STORE", str(tmp_path / "q.json"))
    monkeypatch.setenv("PROMO_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(game, "_default", FakeGame())
    monkeypatch.setattr(render, "render", fake_render())
    return tmp_path


def test_render_writes_video_cover_and_texts(env, capsys):
    assert cli.main(["render", "--format", "who_is", "--lang", "it", "--day", "2026-09-10", "--out", "o"]) == 0
    data = json.loads((env / "o" / "2026-09-10-who_is-it.json").read_text())
    assert data["cards"][0]["player_name"] == "Zlatan Ibrahimović"
    assert (env / "o" / "2026-09-10-who_is-it.txt").read_text().startswith(data["caption"])


def test_render_refuses_spoilers(env, capsys):
    assert cli.main(["render", "--day", "2026-09-24"]) == 2
    assert "non e' ancora chiusa" in capsys.readouterr().err


def test_drafts_need_promo_enabled(env, capsys, monkeypatch):
    assert cli.main(["drafts"]) == 0
    assert "PROMO_ENABLED" in capsys.readouterr().out
    assert not (env / "q.json").exists()


def test_full_flow_drafts_approve_publish_dry_run(env, capsys, monkeypatch):
    monkeypatch.setenv("PROMO_ENABLED", "true")
    assert cli.main(["drafts"]) == 0
    posts = json.loads((env / "q.json").read_text())
    assert len(posts) == 4 and {p["status"] for p in posts.values()} == {"draft"}
    pid = sorted(posts)[0]
    with pytest.raises(SystemExit):
        cli.main(["approve", pid])  # manca --by
    assert cli.main(["approve", pid, "--by", "michele"]) == 0
    assert json.loads((env / "q.json").read_text())[pid]["approved_by"] == "michele"
    capsys.readouterr()
    assert cli.main(["publish", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "(dry-run)" in out or "niente da pubblicare" in out
    assert json.loads((env / "q.json").read_text())[pid]["status"] == "approved"


def test_rome_hour_gate(env, capsys, monkeypatch):
    monkeypatch.setenv("PROMO_ENABLED", "true")
    monkeypatch.setattr(cli, "_rome_hour_ok", lambda hours: False)
    assert cli.main(["drafts", "--at-rome-hour", "7"]) == 0
    assert "fuori dall'ora" in capsys.readouterr().out


def test_brief_is_declared(env, capsys):
    cli.main(["brief", "--lang", "es"])
    assert "#publicidad" in capsys.readouterr().out


def test_doctor_flags_unknown_source(env, capsys):
    cli.main(["doctor"])
    assert "telegram_channel" in capsys.readouterr().out
