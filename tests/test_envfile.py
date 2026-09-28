from promo import envfile


def test_update_keeps_comments_and_unknown_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# commento\nPROMO_ENABLED=false\nALTRO=1\nBOT_TOKEN=abc\n", encoding="utf-8")
    environ = {"BOT_TOKEN": "abc"}
    envfile.update(path, {"PROMO_ENABLED": "true", "BOT_TOKEN": None, "GAME_REPO_PATH": r"C:\Dev\my game"},
                   environ=environ)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# commento\nPROMO_ENABLED=true\nALTRO=1\n") and "BOT_TOKEN" not in text
    assert envfile.read(path) == {"PROMO_ENABLED": "true", "ALTRO": "1", "GAME_REPO_PATH": r"C:\Dev\my game"}
    assert environ == {"PROMO_ENABLED": "true", "GAME_REPO_PATH": r"C:\Dev\my game"}


def test_read_missing_file(tmp_path):
    assert envfile.read(tmp_path / "nope") == {}
