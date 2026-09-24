import pytest
from fakes import TODAY, FakeGame, player

from promo import picker
from promo.models import Card


def test_never_picks_today_or_future_challenges():
    game = FakeGame()
    for fmt in ("who_is", "percent", "journeyman", "solution"):
        for lang in ("it", "en", "es"):
            for seed in range(30):
                card = picker.pick(game, fmt, lang, seed=str(seed))
                if card.source_day:
                    assert card.source_day < TODAY
                assert card.player_id not in {"future", "unused"}


def test_pool_only_uses_practice_only_players():
    game = FakeGame(challenges=[])  # niente sfide passate: resta solo il pool
    for seed in range(30):
        card = picker.pick(game, "who_is", "it", seed=str(seed))
        assert card.source_key == "pool:reserved"


def test_ladder_is_spoiler_free_and_ordered():
    game = FakeGame()
    cards = picker.pick_ladder(game, "it", seed="x")
    assert len(cards) == 4 and len({c.player_id for c in cards}) == 4
    assert all(c.player_id not in {"future", "unused"} for c in cards)
    assert [picker._band(c) for c in cards] == list(picker.LADDER_BANDS)


def test_explicit_day_must_be_closed():
    game = FakeGame()
    with pytest.raises(picker.SpoilerError):
        picker.card_for_day(game, TODAY, "it")
    with pytest.raises(picker.SpoilerError):
        picker.card_for_day(game, "2026-09-25", "it")
    card = picker.card_for_day(game, "2026-09-10", "it")
    assert card.player_name == "Zlatan Ibrahimović" and card.percent_solved == 30


def test_explicit_pool_player_must_be_reserved():
    game = FakeGame()
    with pytest.raises(picker.SpoilerError):
        picker.card_for_pool_player(game, "unused", "it")
    assert picker.card_for_pool_player(game, "reserved", "it").source_key == "pool:reserved"


def test_assert_safe_rejects_forged_cards():
    game = FakeGame()
    with pytest.raises(picker.SpoilerError):
        picker.assert_safe(Card("unused", "x", (), "pool:unused"), game)
    with pytest.raises(picker.SpoilerError):
        picker.assert_safe(Card("future", "x", (), "day:" + TODAY), game)
    with pytest.raises(picker.SpoilerError):
        picker.assert_safe(Card("x", "x", (), "random"), game)


def test_percent_never_invented():
    game = FakeGame()
    assert picker.card_for_day(game, "2026-09-21", "it").percent_solved is None
    for seed in range(20):
        card = picker.pick(game, "percent", "it", seed=str(seed))
        assert card.percent_solved is not None and card.players_count >= picker.MIN_PLAYERS_FOR_PERCENT


def test_percent_math():
    assert picker.percent_solved(0, 0) is None
    assert picker.percent_solved(40, 12) == 30
    assert picker.percent_solved(3, 5) == 100


def test_english_prefers_premier_league_paths():
    game = FakeGame()
    picks = [picker.pick(game, "who_is", "en", seed=str(s)).player_id for s in range(40)]
    assert picks.count("pl") > picks.count("zlatan")


def test_popularity_five_is_disfavoured():
    game = FakeGame()
    picks = [picker.pick(game, "who_is", "it", seed=str(s)).player_id for s in range(40)]
    assert picks.count("zlatan") < len(picks) / 4


def test_pick_is_deterministic_and_respects_exclusions():
    game = FakeGame()
    a = picker.pick(game, "who_is", "it", seed="2026-09-24")
    b = picker.pick(game, "who_is", "it", seed="2026-09-24")
    assert a == b
    c = picker.pick(game, "who_is", "it", seed="2026-09-24", exclude_player_ids=[a.player_id])
    assert c.player_id != a.player_id


def test_journeyman_needs_ten_clubs():
    game = FakeGame()
    card = picker.pick(game, "journeyman", "it", seed="s")
    assert len(card.stops) >= 10


def test_check_format_rejects_out_of_range():
    long = player("long", "Long", [f"Club {i}" for i in range(30)], practice_only=True)
    game = FakeGame(players=[long], challenges=[])
    card = picker.card_for_pool_player(game, "long", "it")
    with pytest.raises(picker.NoMaterial):
        picker.check_format(card, "who_is")


def test_no_material_raises():
    game = FakeGame(players=[], challenges=[])
    with pytest.raises(picker.NoMaterial):
        picker.pick(game, "who_is", "it")


def test_offline_game_repo_uses_only_reserved_pool(tmp_path):
    from promo.game import GameRepo, GameUnavailable

    services = tmp_path / "services"
    services.mkdir()
    (services / "player_pool.py").write_text("")
    repo = GameRepo.__new__(GameRepo)
    repo.offline = True
    assert repo.past_challenges("2026-09-24", 10) == [] and repo.challenge("2026-09-10") is None
    with pytest.raises(GameUnavailable):
        repo.firestore_db()
