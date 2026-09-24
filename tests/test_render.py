"""Rendering vero con ffmpeg, su percorsi corti per stare nei tempi della CI."""
import re
import subprocess

import pytest
from fakes import FakeGame, player

from promo import picker, render
from promo.render import engine, formats
from promo.render.engine import Theme

pytestmark = pytest.mark.render


def _theme(game):
    return Theme.from_game(game)


def _probe(path):
    out = subprocess.run([engine.ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return {
        "duration": int(h) * 3600 + int(m) * 60 + float(s),
        "video": re.search(r"Video: h264.*?(\d{3,4})x(\d{3,4}).*?(\d+(?:\.\d+)?) fps", out),
        "audio": re.search(r"Audio: aac", out),
    }


def _short_game():
    short = player("short", "Short Path", ["Ajax", "Inter", "Genoa"], practice_only=True)
    return FakeGame(players=[short], challenges=[])


def test_solution_video_duration_resolution_audio_and_determinism(tmp_path):
    game = _short_game()
    card = picker.card_for_pool_player(game, "short", "it")
    a = render.render("solution", [card], "it", _theme(game), tmp_path / "a", preset="ultrafast")
    b = render.render("solution", [card], "it", _theme(game), tmp_path / "b", preset="ultrafast")
    info = _probe(a.video_path)
    assert 8 <= info["duration"] <= 12.1
    assert info["video"] and info["video"].group(1, 2) == ("1080", "1920")
    assert float(info["video"].group(3)) == 30
    assert info["audio"]
    assert a.sha256 == b.sha256
    assert (tmp_path / "a" / "pool-short-solution-it.png").exists()


@pytest.mark.parametrize("fmt", ["who_is", "percent", "journeyman", "ladder", "solution"])
@pytest.mark.parametrize("lang", ["it", "en", "es"])
def test_every_format_fits_duration_and_safe_zone(fmt, lang):
    game = FakeGame()
    cards = picker.pick_ladder(game, lang, seed="s") if fmt == "ladder" else [picker.pick(game, fmt, lang, seed="s")]
    theme = _theme(game)
    timeline = formats.build(fmt, cards, lang, theme, "@guess_the_player_from_path_bot")
    low, high = formats.DURATION_LIMITS[fmt]
    assert low <= timeline.total <= high + 1e-6, (fmt, timeline.total)
    engine.check_layout(timeline, theme, step=0.5)


def test_layout_check_catches_text_in_tiktok_buttons():
    game = FakeGame()
    theme = _theme(game)
    timeline = engine.Timeline()
    timeline.add(1.0, lambda canvas, t, k: canvas.text((1000, 500), "X", theme.font(60), (255, 255, 255)))
    with pytest.raises(engine.LayoutError):
        engine.check_layout(timeline, theme)


def test_audio_is_deterministic():
    timeline = engine.Timeline()
    timeline.add(1.0, lambda c, t, k: None)
    timeline.tone(0.1, 880)
    assert engine.synth_audio(timeline, 30) == engine.synth_audio(timeline, 30)
