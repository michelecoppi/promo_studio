"""`python -m promo <comando>`.

Comandi:
  render        genera un video + copertina + testi (M1, M5)
  brief         bozza di testo per un creator pagato, con #adv (da copiare a mano)
  drafts        genera le bozze del giorno in coda `promo_posts` (M2, M7)
  list          elenca i post in coda
  approve / reject / edit-caption   azioni dell'admin (M2)
  publish       pubblica gli `approved` in scadenza (M3, M4); --dry-run non chiama nessuno
  report        report settimanale in Markdown (M6)
  doctor        controlla la configurazione
"""
import argparse
import json
import sys
from pathlib import Path

from promo import copy as promo_copy
from promo import log
from promo.config import LANGUAGES, load
from promo.models import CHANNELS, FORMATS, STATUSES


def _game(settings):
    from promo import game
    return game.default(settings.game_repo_path)


def _theme(game_source):
    from promo.render.engine import Theme
    return Theme.from_game(game_source)


def _store(settings):
    from promo import store
    return store.open_store(settings)


def _rome_hour_ok(hours) -> bool:
    """Per i workflow programmati: GitHub usa cron in UTC, qui si decide nell'ora di Roma."""
    if not hours:
        return True
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).hour in hours


def _hours(value):
    return {int(v) for v in value.split(",")} if value else set()


# ---------------------------------------------------------------------------------------

def cmd_render(args, settings) -> int:
    from promo import picker, render

    game_source = _game(settings)
    lang = args.lang
    seed = args.seed or game_source.today()
    if args.format == "ladder":
        cards = picker.pick_ladder(game_source, lang, seed=seed)
    elif args.day:
        cards = [picker.card_for_day(game_source, args.day, lang)]
    elif args.pool_player:
        cards = [picker.card_for_pool_player(game_source, args.pool_player, lang)]
    else:
        cards = [picker.pick(game_source, args.format, lang, seed=seed)]

    for card in cards:
        picker.check_format(card, args.format)
    out = Path(args.out)
    result = render.render(args.format, cards, lang, _theme(game_source), out, check=args.check_layout,
                           preset=args.preset)
    texts = promo_copy.build(args.format, lang, args.channel, cards, seed=seed, sponsored=args.sponsored)
    base = Path(result.video_path).with_suffix("")
    payload = {
        "format": args.format,
        "language": lang,
        "channel": args.channel,
        "cards": [card.to_dict() for card in cards],
        "caption": texts.caption,
        "hashtags": texts.hashtags,
        "pinned_comment": texts.pinned_comment,
        "tracking_link": texts.tracking_link,
        "video": result.video_path,
        "cover": result.cover_path,
        "duration": result.duration,
        "sha256": result.sha256,
    }
    base.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    base.with_suffix(".txt").write_text(
        f"{texts.caption_with_hashtags()}\n\n--- commento fissato (il giorno dopo) ---\n{texts.pinned_comment}\n\n"
        f"--- link tracciato ---\n{texts.tracking_link}\n",
        encoding="utf-8",
    )
    print(f"video:     {result.video_path} ({result.duration:.1f} s, generato in {result.extra.get('seconds', '?')} s)")
    print(f"copertina: {result.cover_path}")
    print(f"testi:     {base.with_suffix('.txt')}")
    return 0


def cmd_brief(args, settings) -> int:
    print(promo_copy.creator_brief(args.lang, args.source))
    return 0


def cmd_drafts(args, settings) -> int:
    if not _rome_hour_ok(_hours(args.at_rome_hour)):
        print("fuori dall'ora prevista (Europe/Rome): niente da fare")
        return 0
    if not settings.enabled and not args.dry_run:
        print("PROMO_ENABLED non attivo: nessuna bozza generata (usa --dry-run per provare)")
        return 0
    from promo import plan

    game_source = _game(settings)
    created = plan.generate_drafts(
        settings, game_source, _store(settings), _theme(game_source),
        day=args.day or None, fmt=args.format or None, dry_run=args.dry_run,
    )
    for line in created:
        print(line)
    return 0


def cmd_list(args, settings) -> int:
    posts = _store(settings).list(status=args.status or None)
    for post in sorted(posts, key=lambda p: (p.get("created_at") or "", p["id"])):
        print(f"{post['status']:<10} {post['id']:<45} {post.get('scheduled_for') or '':<26} {post.get('caption', '')[:60]}")
    if not posts:
        print("(nessun post)")
    return 0


def _actor(args, settings) -> str:
    actor = (args.by or settings.admin_name).strip()
    if not actor:
        raise SystemExit("serve --by <nome> (o PROMO_ADMIN_NAME): ogni cambio di stato registra chi lo fa")
    return actor


def cmd_approve(args, settings) -> int:
    from promo import queue
    post = queue.approve(_store(settings), args.id, _actor(args, settings))
    print(f"{post['id']}: {post['status']}")
    return 0


def cmd_reject(args, settings) -> int:
    from promo import queue
    post = queue.reject(_store(settings), args.id, _actor(args, settings), reason=args.reason)
    print(f"{post['id']}: {post['status']}")
    return 0


def cmd_edit_caption(args, settings) -> int:
    from promo import queue
    hashtags = args.hashtags.split() if args.hashtags is not None else None
    post = queue.edit_copy(_store(settings), args.id, _actor(args, settings), caption=args.caption,
                           hashtags=hashtags, pinned_comment=args.pinned_comment)
    print(f"{post['id']}: didascalia aggiornata")
    return 0


def cmd_publish(args, settings) -> int:
    if not _rome_hour_ok(_hours(args.at_rome_hour)):
        print("fuori dall'ora prevista (Europe/Rome): niente da fare")
        return 0
    if not settings.enabled and not args.dry_run:
        print("PROMO_ENABLED non attivo: niente pubblicato (usa --dry-run per provare)")
        return 0
    from promo import plan
    from promo.publishers import build_publishers

    game_source = _game(settings)
    results = plan.publish_due(
        _store(settings), build_publishers(settings), _theme(game_source), settings,
        dry_run=args.dry_run, only_id=args.id or None,
    )
    for line in results:
        print(line)
    return 0


def cmd_report(args, settings) -> int:
    if not _rome_hour_ok(_hours(args.at_rome_hour)):
        print("fuori dall'ora prevista (Europe/Rome): niente da fare")
        return 0
    from promo import report

    game_source = _game(settings)
    store = None
    try:
        store = _store(settings)
    except Exception as e:  # il report deve uscire anche senza coda raggiungibile
        log.warning("coda promo_posts non disponibile per il report: %s", e)
    result = report.weekly(game_source, store, settings, end_day=args.end or None)
    path = report.write(result, settings.reports_dir)
    print(f"report: {path}")
    if args.notify:
        report.notify_admin(settings, path)
        print("inviato all'admin su Telegram")
    return 0


def cmd_doctor(args, settings) -> int:
    from promo import status

    game_source, game_error = None, None
    try:
        game_source = _game(settings)
    except Exception as e:
        game_error = str(e)
    icons = {status.OK: "OK  ", status.WARN: "WARN", status.ERROR: "ERR ", status.OFF: "off "}
    results = status.checks(settings, game_source, game_error)
    for check in results:
        print(f"[{icons[check.level]}] {check.area}: {check.name} - {check.detail}")
        if check.fix and check.level in (status.WARN, status.ERROR):
            print(f"         -> {check.fix}")
    counts = status.summary(results)
    print(f"\n{counts['ok']} ok, {counts['warn']} da sistemare, {counts['error']} errori, {counts['off']} spenti")
    return 1 if counts["error"] else 0


# ---------------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m promo", description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = root.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render", help="genera video + copertina + testi")
    p.add_argument("--format", choices=FORMATS, default="who_is")
    p.add_argument("--lang", choices=LANGUAGES, default="it")
    p.add_argument("--day", help="giornata gia' chiusa da usare (YYYY-MM-DD)")
    p.add_argument("--pool-player", help="id di un giocatore del pool riservato (practice_only)")
    p.add_argument("--channel", choices=CHANNELS, default="tiktok", help="canale per il link tracciato")
    p.add_argument("--seed", default="", help="seme della selezione (default: la data di oggi)")
    p.add_argument("--sponsored", action="store_true", help="aggiunge la dichiarazione #adv")
    p.add_argument("--out", default="out")
    p.add_argument("--preset", default="medium", help="preset x264 (medium, fast, veryfast)")
    p.add_argument("--check-layout", action="store_true", help="verifica la zona sicura prima di codificare")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("brief", help="bozza per un creator pagato (#adv), da copiare a mano")
    p.add_argument("--lang", choices=LANGUAGES, default="it")
    p.add_argument("--source", default="creator", help="valore src_ del link")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("drafts", help="genera le bozze del giorno")
    p.add_argument("--day", help="giorno di pubblicazione (default: oggi)")
    p.add_argument("--format", choices=[f for f in FORMATS if f != "solution"])
    p.add_argument("--dry-run", action="store_true", help="genera i file ma non scrive la coda")
    p.add_argument("--at-rome-hour", default="", help="esegui solo in queste ore di Roma (es. 7,8)")
    p.set_defaults(func=cmd_drafts)

    p = sub.add_parser("list", help="elenca i post in coda")
    p.add_argument("--status", choices=STATUSES)
    p.set_defaults(func=cmd_list)

    for name, func in (("approve", cmd_approve), ("reject", cmd_reject)):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--by", default="", help="chi approva/rifiuta (default PROMO_ADMIN_NAME)")
        if name == "reject":
            p.add_argument("--reason", default="")
        p.set_defaults(func=func)

    p = sub.add_parser("edit-caption")
    p.add_argument("id")
    p.add_argument("--by", default="")
    p.add_argument("--caption")
    p.add_argument("--hashtags", help="separati da spazio")
    p.add_argument("--pinned-comment")
    p.set_defaults(func=cmd_edit_caption)

    p = sub.add_parser("publish", help="pubblica gli approved in scadenza")
    p.add_argument("--dry-run", action="store_true", help="fa tutto tranne la chiamata esterna")
    p.add_argument("--id", help="solo questo post")
    p.add_argument("--at-rome-hour", default="")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("report", help="report settimanale")
    p.add_argument("--end", help="ultimo giorno escluso della settimana (default: oggi)")
    p.add_argument("--notify", action="store_true", help="invia il report all'admin su Telegram")
    p.add_argument("--at-rome-hour", default="")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("doctor", help="controlla la configurazione")
    p.set_defaults(func=cmd_doctor)
    return root


def main(argv=None) -> int:
    log.configure()
    settings = load()
    log.register_secrets(settings.secret_values())
    args = parser().parse_args(argv)
    try:
        return args.func(args, settings)
    except Exception as e:  # messaggio leggibile e senza segreti, non un traceback
        from promo.game import GameUnavailable
        from promo.picker import NoMaterial, SpoilerError
        from promo.store import NotFound
        if isinstance(e, (GameUnavailable, NoMaterial, SpoilerError, ValueError, NotFound)):
            print(f"errore: {log.scrub(e)}", file=sys.stderr)
            return 2
        raise
