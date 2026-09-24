"""Configurazione da variabili d'ambiente: un solo posto, letto una volta.

I segreti (token Telegram, TikTok, PostHog) si leggono qui e non si stampano mai: `Settings`
ha un `__repr__` che li oscura, cosi' un `print(settings)` o un traceback non li espone.
"""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping, Optional

BOT_USERNAME = "guess_the_player_from_path_bot"
LANGUAGES = ("it", "en", "es")
DEFAULT_LANGUAGES = LANGUAGES

_SECRET_FIELDS = frozenset({
    "bot_token", "tiktok_client_key", "tiktok_client_secret", "tiktok_refresh_token",
})


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: Optional[str], default: tuple) -> tuple:
    items = tuple(item.strip() for item in (value or "").split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    languages: tuple = DEFAULT_LANGUAGES
    # Su quali canali finisce la bozza di ogni lingua. Il canale Telegram e' uno solo, quindi
    # di default riceve solo l'italiano; TikTok riceve tutte le lingue.
    telegram_languages: tuple = ("it",)
    telegram_channel_id: str = ""
    admin_chat_id: str = ""
    admin_name: str = ""
    bot_token: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_refresh_token: str = ""
    tiktok_refresh_token_secret: str = ""
    x_enabled: bool = False
    game_repo_path: str = ""
    store: str = "firestore"
    local_store_path: Path = Path("promo_posts.json")
    media_dir: Path = Path("promo-media")
    costs_file: Path = Path("promo_costs.json")
    reports_dir: Path = Path("reports")
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ
        languages = tuple(lang for lang in _csv(env.get("PROMO_LANGUAGES"), DEFAULT_LANGUAGES) if lang in LANGUAGES)
        return cls(
            enabled=_flag(env.get("PROMO_ENABLED")),
            languages=languages or DEFAULT_LANGUAGES,
            telegram_languages=_csv(env.get("PROMO_TELEGRAM_LANGUAGES"), ("it",)),
            telegram_channel_id=(env.get("PROMO_TELEGRAM_CHANNEL_ID") or "").strip(),
            admin_chat_id=(env.get("PROMO_ADMIN_CHAT_ID") or "").strip(),
            admin_name=(env.get("PROMO_ADMIN_NAME") or "").strip(),
            bot_token=(env.get("BOT_TOKEN") or "").strip(),
            tiktok_client_key=(env.get("TIKTOK_CLIENT_KEY") or "").strip(),
            tiktok_client_secret=(env.get("TIKTOK_CLIENT_SECRET") or "").strip(),
            tiktok_refresh_token=(env.get("TIKTOK_REFRESH_TOKEN") or "").strip(),
            tiktok_refresh_token_secret=(env.get("TIKTOK_REFRESH_TOKEN_SECRET") or "").strip(),
            x_enabled=_flag(env.get("PROMO_X_ENABLED")),
            game_repo_path=(env.get("GAME_REPO_PATH") or "").strip(),
            store=(env.get("PROMO_STORE") or "firestore").strip().lower(),
            local_store_path=Path(env.get("PROMO_LOCAL_STORE") or "promo_posts.json"),
            media_dir=Path(env.get("PROMO_MEDIA_DIR") or "promo-media"),
            costs_file=Path(env.get("PROMO_COSTS_FILE") or "promo_costs.json"),
            reports_dir=Path(env.get("PROMO_REPORTS_DIR") or "reports"),
        )

    def __repr__(self) -> str:
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _SECRET_FIELDS and value:
                value = "[REDACTED]"
            parts.append(f"{f.name}={value!r}")
        return f"Settings({', '.join(parts)})"

    def secret_values(self) -> tuple:
        """I valori da non far mai uscire nei log (vedi promo.log.scrub)."""
        return tuple(getattr(self, name) for name in _SECRET_FIELDS if getattr(self, name))


def load(environ: Optional[Mapping[str, str]] = None) -> Settings:
    if environ is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:  # pragma: no cover - python-dotenv e' in requirements.txt
            pass
    return Settings.from_env(environ)
