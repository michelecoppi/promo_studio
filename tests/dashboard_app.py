"""App Streamlit di prova per tests/test_dashboard.py: dashboard vera, gioco e video finti."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import streamlit as st  # noqa: E402
from fakes import FakeGame  # noqa: E402
from helpers import fake_render  # noqa: E402

from admin.promo_page import render_page  # noqa: E402
from promo import render  # noqa: E402
from promo.config import Settings  # noqa: E402
from promo.render.engine import Theme  # noqa: E402
from promo.store import JsonFileStore  # noqa: E402

st.set_page_config(page_title="Promo Studio", layout="wide")
render.render = fake_render()
tmp = os.environ["PROMO_TEST_DIR"]
settings = Settings(enabled=True, admin_name="michele", store="local", media_dir=__import__("pathlib").Path(tmp) / "media",
                    costs_file=__import__("pathlib").Path(tmp) / "costs.json",
                    reports_dir=__import__("pathlib").Path(tmp) / "reports",
                    telegram_channel_id="@c", bot_token="1:x")
game = FakeGame()
render_page(JsonFileStore(os.path.join(tmp, "q.json")), Theme.from_game(game), settings, game)
