"""Dashboard locale del Promo Studio: `streamlit run admin/app.py`.

Gira sulla macchina del maintainer con lo stesso .env del gioco (credenziali Firestore) piu'
GAME_REPO_PATH. Nessun hosting: come admin_ui.py del gioco.
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

st.set_page_config(page_title="Promo Studio", layout="wide")
settings = load()
log.register_secrets(settings.secret_values())
try:
    game_source = game.default(settings.game_repo_path)
    render_page(store.open_store(settings, game_source), Theme.from_game(game_source), settings)
except game.GameUnavailable as e:
    st.error(str(e))
