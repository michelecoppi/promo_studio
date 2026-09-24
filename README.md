# Promo Studio — Guess the Player from the Path

Strumento che prepara (e, dopo l'approvazione di una persona, pubblica) i contenuti promozionali di
[@guess_the_player_from_path_bot](https://t.me/guess_the_player_from_path_bot): video verticali e testi
in italiano, inglese e spagnolo, più un report settimanale sui canali che portano giocatori.

**La macchina prepara, la persona approva.** Documentazione completa: [`docs/promo-studio.md`](docs/promo-studio.md).
Demo passo per passo (anche senza credenziali): [`docs/demo-setup.md`](docs/demo-setup.md).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export GAME_REPO_PATH=../guess_the_player_from_the_path   # checkout del gioco
pip install -r "$GAME_REPO_PATH/requirements.txt"
python -m promo render --format who_is --lang it --day 2026-09-10

pip install -r requirements-admin.txt
streamlit run admin/app.py        # dashboard: stato, coda, genera, pubblica, report, guida
```

Licenza: PolyForm Noncommercial 1.0.0 (vedi `LICENSE`).
