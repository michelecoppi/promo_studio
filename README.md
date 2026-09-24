# Promo Studio — Guess the Player from the Path

Strumento che prepara (e, dopo l'approvazione di una persona, pubblica) i contenuti promozionali di
[@guess_the_player_from_path_bot](https://t.me/guess_the_player_from_path_bot): video verticali e testi
in italiano, inglese e spagnolo, più un report settimanale sui canali che portano giocatori.

**La macchina prepara, la persona approva.** Documentazione completa: [`docs/promo-studio.md`](docs/promo-studio.md).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export GAME_REPO_PATH=../guess_the_player_from_the_path   # checkout del gioco
pip install -r "$GAME_REPO_PATH/requirements.txt"
python -m promo render --format who_is --lang it --day 2026-09-10
```

Licenza: PolyForm Noncommercial 1.0.0 (vedi `LICENSE`).
