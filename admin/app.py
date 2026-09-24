"""Dashboard locale del Promo Studio: `streamlit run admin/app.py`.

Gira sulla macchina del maintainer con lo stesso .env del gioco (credenziali Firestore) piu'
GAME_REPO_PATH. Nessun hosting: come admin_ui.py del gioco. Se il gioco o la coda non sono
raggiungibili la pagina si apre lo stesso e dice cosa manca (schede Stato e Guida).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from admin.promo_page import render_page  # noqa: E402
from promo import game, log, store  # noqa: E402
from promo.config import load  # noqa: E402
from promo.render.engine import Theme  # noqa: E402

st.set_page_config(page_title="Promo Studio", page_icon="📣", layout="wide")
settings = load()
log.register_secrets(settings.secret_values())

game_source = game_error = theme = queue_store = store_error = None
try:
    game_source = game.default(settings.game_repo_path)
    theme = Theme.from_game(game_source)
except game.GameUnavailable as e:
    game_error = str(e)
try:
    queue_store = store.open_store(settings, game_source) if (game_source or settings.store == "local") else None
    if queue_store is None:
        store_error = "serve il repository del gioco per raggiungere Firestore (o PROMO_STORE=local)"
except Exception as e:
    store_error = log.scrub(e)

render_page(queue_store, theme, settings, game_source, game_error, store_error)
