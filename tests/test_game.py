"""`promo.game.GameRepo` senza il gioco vero: cartelle e moduli finti, niente rete."""
import sys
import types

import pytest

from promo.game import TITLE_FONT_CANDIDATES, AnalyticsError, GameRepo


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


class QueryError(Exception):
    pass


@pytest.fixture
def fake_paq(monkeypatch):
    """`services.product_analytics_query` finto: solo l'API pubblica del gioco (`run_hogql`)."""
    calls = []
    paq = types.ModuleType("services.product_analytics_query")
    paq.QueryError = QueryError

    def run_hogql(query, config=None):
        calls.append((query, config))
        if "boom" in query:
            raise QueryError("PostHog non configurato")
        return [["tiktok", 3]]

    paq.run_hogql = run_hogql
    services = types.ModuleType("services")
    services.product_analytics_query = paq
    monkeypatch.setitem(sys.modules, "services", services)
    monkeypatch.setitem(sys.modules, "services.product_analytics_query", paq)
    return calls


def test_hogql_uses_the_public_run_hogql(tmp_path, fake_paq):
    assert repo_at(tmp_path).hogql("SELECT 1") == [["tiktok", 3]]
    assert fake_paq == [("SELECT 1", None)]


def test_hogql_errors_become_analytics_error(tmp_path, fake_paq):
    with pytest.raises(AnalyticsError, match="non configurato"):
        repo_at(tmp_path).hogql("SELECT boom")


def test_path_image_uses_the_public_names(tmp_path):
    """Solo le funzioni pubbliche del gioco (#234): gli alias privati spariranno."""
    repo = repo_at(tmp_path)
    repo._path_image = types.SimpleNamespace(years_label=lambda stop: f"{stop['from']}-{stop['to']}",
                                             color_for_team=lambda team: (1, 2, 3))
    assert repo.years_label({"from": 2010, "to": 2012}) == "2010-2012"
    assert repo.team_color("Juventus") == (1, 2, 3)
