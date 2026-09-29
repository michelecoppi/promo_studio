import json

import pytest
from fakes import FakeGame
from helpers import NOW, fake_render, settings

from promo import briefs, cli, copy, game, plan, render
from promo.store import MemoryStore

BRIEF = {
    "campaign_id": "w40-it",
    "language": "it",
    "format": "who_is",
    "channel": "tiktok",
    "cta": "Gioca sul bot",
    "angle": "i giramondo della Serie A",
    "facts": ["ha giocato in 5 squadre"],
}


def brief(**kw):
    data = dict(BRIEF)
    data.update(kw)
    return briefs.validate(data)


def test_import_creates_one_draft_with_brief_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    store = MemoryStore()
    lines = briefs.import_brief(settings(tmp_path), FakeGame(), store, None, brief(), now=NOW)
    (post,) = store.list()
    assert post["status"] == "draft" and post["format"] == "who_is" and post["language"] == "it"
    assert post["channel"] == "tiktok" and post["brief_id"] == "w40-it" and post["id"].endswith("-w40-it")
    assert post["brief"] == {"cta": "Gioca sul bot", "angle": "i giramondo della Serie A",
                             "facts": ["ha giocato in 5 squadre"]}
    # il testo non viene dal brief, ma dai template; il link resta quello di oggi
    assert "giramondo" not in post["caption"] and "Gioca sul bot" not in post["caption"]
    assert post["tracking_link"] == copy.tracking_link("tiktok")
    assert "creata" in lines[0]


def test_import_is_idempotent_per_campaign_and_day(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(render, "render", fake_render(calls))
    store, cfg = MemoryStore(), settings(tmp_path)
    briefs.import_brief(cfg, FakeGame(), store, None, brief(), now=NOW)
    lines = briefs.import_brief(cfg, FakeGame(), store, None, brief(), now=NOW)
    assert len(store.list()) == 1 and len(calls) == 1
    assert "gia' presenti" in lines[0]
    # un'altra campagna lo stesso giorno e' un'altra bozza
    briefs.import_brief(cfg, FakeGame(), store, None, brief(campaign_id="w40-bis"), now=NOW)
    assert len(store.list()) == 2


def test_campaign_links_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    store = MemoryStore()
    briefs.import_brief(settings(tmp_path, campaign_links=True), FakeGame(), store, None,
                        brief(channel="telegram_channel"), now=NOW)
    (post,) = store.list()
    assert post["tracking_link"].endswith("?start=src_telegram_channel-w40-it")
    assert post["pinned_comment"].endswith(post["tracking_link"])


def test_brief_drafts_do_not_block_the_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "render", fake_render())
    store, cfg = MemoryStore(), settings(tmp_path, languages=("it",), telegram_languages=())
    briefs.import_brief(cfg, FakeGame(), store, None, brief(), now=NOW)
    plan.generate_drafts(cfg, FakeGame(), store, None, now=NOW)
    posts = store.list()
    assert len(posts) == 2 and sum(1 for p in posts if p["brief_id"]) == 1
    # il picker esclude il giocatore gia' usato dal brief
    assert posts[0]["player_ids"] != posts[1]["player_ids"]


@pytest.mark.parametrize("key,value,message", [
    ("format", "reel", "'format'"),
    ("format", "solution", "'format'"),
    ("channel", "instagram", "'channel'"),
    ("language", "fr", "'language'"),
    ("campaign_id", "", "manca 'campaign_id'"),
    ("campaign_id", "w40 it", "'campaign_id'"),
    ("campaign_id", "W40-it", "'campaign_id'"),
    ("campaign_id", "w40_it", "'campaign_id'"),
    ("campaign_id", "x" * 25, "'campaign_id'"),
    ("facts", "un fatto", "'facts'"),
    ("day", "domani", "'day'"),
])
def test_invalid_values_are_rejected_clearly(key, value, message):
    with pytest.raises(briefs.BriefError) as e:
        brief(**{key: value})
    assert message in str(e.value)
    if key in ("format", "channel", "language"):
        assert "ammessi" in str(e.value)


def test_past_day_is_refused(tmp_path):
    with pytest.raises(briefs.BriefError):
        briefs.import_brief(settings(tmp_path), FakeGame(), MemoryStore(), None, brief(day="2026-09-01"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("PROMO_ENABLED", "PROMO_CAMPAIGN_LINKS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROMO_STORE", "local")
    monkeypatch.setenv("PROMO_LOCAL_STORE", str(tmp_path / "q.json"))
    monkeypatch.setenv("PROMO_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(game, "_default", FakeGame())
    monkeypatch.setattr(render, "render", fake_render())
    return tmp_path


def test_cli_brief_import(env, capsys, monkeypatch):
    path = env / "brief.json"
    path.write_text(json.dumps(BRIEF), encoding="utf-8")
    assert cli.main(["brief-import", str(path)]) == 0
    assert "PROMO_ENABLED" in capsys.readouterr().out and not (env / "q.json").exists()

    monkeypatch.setenv("PROMO_ENABLED", "true")
    monkeypatch.setenv("PROMO_CAMPAIGN_LINKS", "true")
    assert cli.main(["brief-import", str(path)]) == 0
    assert cli.main(["brief-import", str(path)]) == 0
    posts = json.loads((env / "q.json").read_text())
    (post,) = posts.values()
    assert post["tracking_link"].endswith("?start=src_tiktok-w40-it")


def test_cli_brief_import_reports_bad_brief(env, capsys):
    path = env / "brief.json"
    path.write_text(json.dumps(dict(BRIEF, channel="instagram")), encoding="utf-8")
    assert cli.main(["brief-import", str(path)]) == 2
    assert "'channel'" in capsys.readouterr().err
    assert cli.main(["brief-import", str(env / "manca.json")]) == 2
