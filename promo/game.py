"""L'unico confine verso il repository del gioco.

Il Promo Studio vive in un repository suo ma **riusa** il codice del gioco invece di
riscriverlo (dataset, sfide passate, ordine e traduzione delle carriere, colori dei club,
font, lettura di PostHog): e' tutto qui, dietro un'interfaccia piccola. Il resto del
pacchetto non importa mai `services.*` direttamente, per due motivi:

- i test girano senza il repository del gioco e senza Firestore, con un `GameSource` finto;
- se un giorno il Promo Studio entra nel repository del gioco come `apps/promo/`, basta
  sostituire questo file.

Il repository del gioco si indica con `GAME_REPO_PATH` (una checkout locale di
`michelecoppi/guess_the_player_from_the_path`), e deve avere le sue dipendenze installate.
"""
import os
import sys
from pathlib import Path
from typing import Optional, Protocol

DEFAULT_PALETTE = {
    "bg_top": (8, 16, 27),
    "bg_bottom": (18, 38, 60),
    "card": (27, 46, 68),
    "track": (44, 68, 92),
    "text": (240, 245, 250),
    "muted": (150, 170, 190),
    "accent": (56, 189, 130),
}

TITLE_FONT_RELATIVE = Path("webapp/src/assets/fonts/BarlowCondensed-SemiBold.ttf")


class GameUnavailable(RuntimeError):
    """Il repository del gioco non e' configurato o non si importa."""


class GameSource(Protocol):
    def today(self) -> str: ...

    def past_challenges(self, before_day: str, limit: int) -> list: ...

    def challenge(self, day: str) -> Optional[dict]: ...

    def reserved_players(self) -> list: ...

    def player(self, player_id: str) -> Optional[dict]: ...

    def difficulty(self, player: dict) -> Optional[str]: ...

    def order_career(self, career: list) -> list: ...

    def localize_career(self, career: list, lang: str) -> list: ...

    def years_label(self, stop: dict) -> str: ...

    def team_color(self, team: str) -> tuple: ...

    def palette(self) -> dict: ...

    def title_font_path(self) -> Optional[str]: ...

    def text_font_path(self) -> Optional[str]: ...

    def campaign_sources(self) -> tuple: ...

    def hogql(self, query: str) -> list: ...

    def firestore_db(self): ...


class GameRepo:
    """Il gioco vero, importato da `GAME_REPO_PATH`."""

    def __init__(self, path: str, offline: bool = False):
        # Offline: niente Firestore. Solo dataset locale e pool riservato (practice_only), che
        # non spoilera per costruzione: serve per demo e prove senza credenziali.
        self.offline = offline
        if not path:
            raise GameUnavailable(
                "GAME_REPO_PATH non impostato: serve una checkout di guess_the_player_from_the_path."
            )
        self.path = Path(path).resolve()
        if not (self.path / "services" / "player_pool.py").exists():
            raise GameUnavailable(f"{self.path} non sembra il repository del gioco (manca services/player_pool.py).")
        if str(self.path) not in sys.path:
            sys.path.insert(0, str(self.path))
        try:
            from services import (
                career_order,
                content_i18n,
                dates,
                path_image,
                player_pool,
            )
        except ImportError as e:
            raise GameUnavailable(
                f"Impossibile importare il codice del gioco da {self.path}: {e}. "
                f"Installa le sue dipendenze: pip install -r {self.path / 'requirements.txt'}"
            ) from e
        self._career_order = career_order
        self._content_i18n = content_i18n
        self._dates = dates
        self._path_image = path_image
        self._player_pool = player_pool
        try:
            from services import observability

            from promo import log
            log.use_game_scrubber(observability.scrub_text)
        except ImportError:
            pass

    # --- date e sfide -------------------------------------------------------------------
    def today(self) -> str:
        return self._dates.today_iso()

    def _fs(self):
        from services import firebase_service
        return firebase_service

    def past_challenges(self, before_day: str, limit: int) -> list:
        if self.offline:
            return []
        return self._fs().get_past_daily_paths(limit=limit, before_day_iso=before_day)

    def challenge(self, day: str) -> Optional[dict]:
        if self.offline:
            return None
        return self._fs().get_daily_path(day)

    def firestore_db(self):
        if self.offline:
            raise GameUnavailable("PROMO_OFFLINE=true: Firestore disattivato, usa PROMO_STORE=local")
        return self._fs().db

    # --- dataset ----------------------------------------------------------------------
    def reserved_players(self) -> list:
        return self._player_pool.get_practice_players()

    def player(self, player_id: str) -> Optional[dict]:
        return self._player_pool.get_player_by_id(player_id) if player_id else None

    def difficulty(self, player: dict) -> Optional[str]:
        from services.difficulty import compute_difficulty
        return compute_difficulty(player) if player else None

    def order_career(self, career: list) -> list:
        return self._career_order.order_career(career)

    def localize_career(self, career: list, lang: str) -> list:
        return self._content_i18n.localize_career(career, lang)

    # --- stile ------------------------------------------------------------------------
    def years_label(self, stop: dict) -> str:
        return self._path_image._years_label(stop)

    def team_color(self, team: str) -> tuple:
        return self._path_image._color_for_team(team)

    def palette(self) -> dict:
        pi = self._path_image
        return dict(
            DEFAULT_PALETTE,
            card=pi.CARD_COLOR,
            track=pi.TRACK_COLOR,
            text=pi.TEXT_COLOR,
            muted=pi.MUTED_COLOR,
            accent=pi.ACCENT_COLOR,
        )

    def title_font_path(self) -> Optional[str]:
        candidate = self.path / TITLE_FONT_RELATIVE
        return str(candidate) if candidate.exists() else None

    def text_font_path(self) -> Optional[str]:
        from services import fonts
        return fonts.font_path(bold=True)

    # --- analytics --------------------------------------------------------------------
    def campaign_sources(self) -> tuple:
        from services import product_analytics
        return tuple(product_analytics.CAMPAIGN_SOURCES)

    def hogql(self, query: str) -> list:
        from services import product_analytics_query as paq
        try:
            return paq._run_hogql(paq.settings(), query)
        except paq.QueryError as e:
            raise AnalyticsError(str(e)) from e


class AnalyticsError(RuntimeError):
    """PostHog non configurato o non raggiungibile."""


_default: Optional[GameSource] = None


def default(path: Optional[str] = None, offline: Optional[bool] = None) -> GameSource:
    global _default
    if _default is None:
        if offline is None:
            offline = (os.environ.get("PROMO_OFFLINE") or "").strip().lower() in {"1", "true", "yes", "on"}
        _default = GameRepo(path or os.environ.get("GAME_REPO_PATH", ""), offline=offline)
    return _default
