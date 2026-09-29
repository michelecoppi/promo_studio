"""Configurazione da variabili d'ambiente: un solo posto, letto una volta.

I segreti (token Telegram, TikTok, PostHog) si leggono qui e non si stampano mai: `Settings`
ha un `__repr__` che li oscura, cosi' un `print(settings)` o un traceback non li espone.
"""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping, Optional

BOT_USERNAME = "guess_the_player_from_path_bot"
# Redirect URI registrato nell'app TikTok (Login Kit): dopo il login TikTok rimanda qui con ?code=.
DEFAULT_TIKTOK_REDIRECT_URI = "https://guess-the-player-595902172561.europe-west1.run.app/privacy"
LANGUAGES = ("it", "en", "es")
DEFAULT_LANGUAGES = LANGUAGES

_SECRET_FIELDS = frozenset({
    "bot_token", "approval_bot_token", "approval_webhook_secret", "github_dispatch_token", "tiktok_client_key", "tiktok_client_secret", "tiktok_refresh_token",
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
    # Bot dedicato alle approvazioni (promo/approvals.py), non quello del gioco.
    approval_bot_token: str = ""
    # Il segreto che Telegram rimanda al webhook delle approvazioni (promo/approval_service.py).
    approval_webhook_secret: str = ""
    # Avvio dei lavori da Cloud Scheduler (promo/dispatch.py): token GitHub, chi puo' chiamare,
    # e l'indirizzo del servizio (audience del token OIDC).
    github_dispatch_token: str = ""
    dispatch_invoker: str = ""
    dispatch_audience: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_refresh_token: str = ""
    tiktok_refresh_token_secret: str = ""
    tiktok_redirect_uri: str = DEFAULT_TIKTOK_REDIRECT_URI
    tiktok_account: str = ""
    x_enabled: bool = False
    # Il bot del gioco sa leggere `?start=src_<canale>-<campaign_id>`? Finche' no, i link delle
    # bozze da brief restano `src_<canale>` (la campagna resta comunque in `brief_id`).
    campaign_links: bool = False
    # Progetto GCP del supervisore (gtp_orchestrator) da cui leggere, in sola lettura, i brief
    # `promo_briefs` (promo/supervisor_briefs.py). Vuoto = funzione spenta.
    supervisor_firestore_project: str = ""
    game_repo_path: str = ""
    store: str = "firestore"
    local_store_path: Path = Path("promo_posts.json")
    media_dir: Path = Path("promo-media")
    costs_file: Path = Path("costs.json")
    reports_dir: Path = Path("reports")
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ
        languages = tuple(lang for lang in _csv(env.get("PROMO_LANGUAGES"), DEFAULT_LANGUAGES) if lang in LANGUAGES)
        return cls(
            enabled=_flag(env.get("PROMO_ENABLED")),
            languages=languages or DEFAULT_LANGUAGES,
            # "none" spegne il canale Telegram (vuoto = default "it").
            telegram_languages=(() if (env.get("PROMO_TELEGRAM_LANGUAGES") or "").strip().lower() == "none"
                                else _csv(env.get("PROMO_TELEGRAM_LANGUAGES"), ("it",))),
            telegram_channel_id=(env.get("PROMO_TELEGRAM_CHANNEL_ID") or "").strip(),
            admin_chat_id=(env.get("PROMO_ADMIN_CHAT_ID") or "").strip(),
            admin_name=(env.get("PROMO_ADMIN_NAME") or "").strip(),
            bot_token=(env.get("BOT_TOKEN") or "").strip(),
            approval_bot_token=(env.get("PROMO_APPROVAL_BOT_TOKEN") or "").strip(),
            approval_webhook_secret=(env.get("PROMO_APPROVAL_WEBHOOK_SECRET") or "").strip(),
            github_dispatch_token=(env.get("PROMO_GITHUB_DISPATCH_TOKEN") or "").strip(),
            dispatch_invoker=(env.get("PROMO_DISPATCH_INVOKER") or "").strip(),
            dispatch_audience=(env.get("PROMO_DISPATCH_AUDIENCE") or "").strip(),
            tiktok_client_key=(env.get("TIKTOK_CLIENT_KEY") or "").strip(),
            tiktok_client_secret=(env.get("TIKTOK_CLIENT_SECRET") or "").strip(),
            tiktok_refresh_token=(env.get("TIKTOK_REFRESH_TOKEN") or "").strip(),
            tiktok_refresh_token_secret=(env.get("TIKTOK_REFRESH_TOKEN_SECRET") or "").strip(),
            tiktok_redirect_uri=(env.get("TIKTOK_REDIRECT_URI") or "").strip() or DEFAULT_TIKTOK_REDIRECT_URI,
            tiktok_account=(env.get("PROMO_TIKTOK_ACCOUNT") or "").strip(),
            x_enabled=_flag(env.get("PROMO_X_ENABLED")),
            campaign_links=_flag(env.get("PROMO_CAMPAIGN_LINKS")),
            supervisor_firestore_project=(env.get("PROMO_SUPERVISOR_FIRESTORE_PROJECT") or "").strip(),
            game_repo_path=(env.get("GAME_REPO_PATH") or "").strip(),
            store=(env.get("PROMO_STORE") or "firestore").strip().lower(),
            local_store_path=Path(env.get("PROMO_LOCAL_STORE") or "promo_posts.json"),
            media_dir=Path(env.get("PROMO_MEDIA_DIR") or "promo-media"),
            costs_file=Path(env.get("PROMO_COSTS_FILE") or "costs.json"),
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
