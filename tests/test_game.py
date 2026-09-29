"""`promo.game.GameRepo` senza il gioco vero: cartelle e moduli finti, niente rete."""
import types

from promo.game import TITLE_FONT_CANDIDATES, GameRepo


def repo_at(path):
    repo = GameRepo.__new__(GameRepo)
    repo.path, repo.offline = path, False
    return repo


def add_font(root, relative):
    font = root / relative
    font.parent.mkdir(parents=True, exist_ok=True)
    font.write_bytes(b"font")
    return font


def test_title_font_found_as_woff2_like_the_game_ships_it(tmp_path):
    woff2 = add_font(tmp_path, "webapp/src/assets/fonts/BarlowCondensed-SemiBold.woff2")
    assert repo_at(tmp_path).title_font_path() == str(woff2)


def test_title_font_prefers_ttf_when_both_exist(tmp_path):
    for relative in TITLE_FONT_CANDIDATES:
        add_font(tmp_path, relative)
    ttf = tmp_path / "webapp/src/assets/fonts/BarlowCondensed-SemiBold.ttf"
    assert repo_at(tmp_path).title_font_path() == str(ttf)


def test_title_font_missing_means_fallback(tmp_path):
    (tmp_path / "webapp/src/assets/fonts").mkdir(parents=True)
    assert repo_at(tmp_path).title_font_path() is None


def test_game_boundary_has_no_posthog_queries():
    """Issue #8: le metriche di prodotto le calcola solo il supervisore, Promo non interroga PostHog."""
    assert not hasattr(GameRepo, "hogql")


def test_path_image_uses_the_public_names(tmp_path):
    """Solo le funzioni pubbliche del gioco (#234): gli alias privati spariranno."""
    repo = repo_at(tmp_path)
    repo._path_image = types.SimpleNamespace(years_label=lambda stop: f"{stop['from']}-{stop['to']}",
                                             color_for_team=lambda team: (1, 2, 3))
    assert repo.years_label({"from": 2010, "to": 2012}) == "2010-2012"
    assert repo.team_color("Juventus") == (1, 2, 3)
