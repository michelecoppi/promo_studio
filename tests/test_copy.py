import re

import pytest
from fakes import FakeGame

from promo import catalog, copy, picker
from promo.config import LANGUAGES
from promo.models import FORMATS


def _placeholders(value):
    if isinstance(value, dict):
        return {k: _placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_placeholders(v) for v in value]
    return sorted(re.findall(r"{(\w+)}", value))


def _shape(value):
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return ["list"]
    return "str"


def test_catalogs_have_same_keys_and_placeholders():
    reference = catalog.load("it")
    for lang in LANGUAGES:
        data = catalog.load(lang)
        assert _shape(data) == _shape(reference), lang
        for section in ("video",):
            assert _placeholders(data[section]) == _placeholders(reference[section]), lang


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_copy_rules_every_language(lang, fmt):
    game = FakeGame()
    cards = picker.pick_ladder(game, lang, seed="s") if fmt == "ladder" else [
        picker.pick(game, fmt, lang, seed="s")]
    for channel in ("tiktok", "telegram_channel", "x"):
        text = copy.build(fmt, lang, channel, cards, seed="s")
        assert 0 < len(text.caption) <= copy.MAX_CAPTION
        assert copy.MIN_HASHTAGS <= len(text.hashtags) <= copy.MAX_HASHTAGS
        assert all(tag.startswith("#") for tag in text.hashtags)
        assert text.tracking_link == f"https://t.me/guess_the_player_from_path_bot?start=src_{copy.SOURCE_FOR_CHANNEL[channel]}"
        assert "{" not in text.caption + text.pinned_comment
        if fmt in ("who_is", "percent", "journeyman"):
            assert cards[0].player_name in text.pinned_comment
        if fmt == "percent":
            assert f"{cards[0].percent_solved}%" in text.caption


@pytest.mark.parametrize("lang,tag", [("it", "#adv"), ("en", "#ad"), ("es", "#publicidad")])
def test_sponsored_is_declared(lang, tag):
    game = FakeGame()
    card = picker.pick(game, "who_is", lang, seed="s")
    text = copy.build("who_is", lang, "tiktok", [card], sponsored=True)
    assert text.caption.startswith(tag)
    assert tag in copy.creator_brief(lang)


def test_long_caption_is_truncated():
    assert len(copy._fit("x" * 400)) == copy.MAX_CAPTION


def test_check_sources_flags_unknown_channels():
    assert copy.check_sources(FakeGame().campaign_sources()) == ["telegram_channel"]
