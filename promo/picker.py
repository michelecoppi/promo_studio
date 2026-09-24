"""Cosa pubblicare: la scelta dei percorsi, con la regola anti-spoiler.

**La regola (§3.1 della specifica).** Un contenuto promozionale mostra un percorso a
migliaia di persone: se fosse quello di una sfida futura, chi l'ha visto la riconoscerebbe
in due secondi. Quindi si usano solo due sorgenti, le stesse di services/practice_content.py:

1. le sfide **gia' chiuse**, cioe' con `day < oggi` nel fuso del gioco (Europe/Rome);
2. il **pool riservato** (`"practice_only": true`), che per costruzione non esce mai come
   sfida del giorno.

Un giocatore del dataset non ancora usato non si sceglie mai, nemmeno se e' perfetto. La
regola e' applicata due volte: nella scelta dei candidati e, alla fine, da `assert_safe()`
su ogni scheda restituita, cosi' un errore in un criterio di selezione non puo' diventare
uno spoiler.
"""
import random
from typing import Iterable, Optional

from promo.game import GameSource
from promo.models import DAY_PREFIX, POOL_PREFIX, Card, Stop

PREFERRED_LEAGUES = {
    "en": frozenset({"Premier League", "La Liga", "Liga Profesional", "Liga MX"}),
    "es": frozenset({"Premier League", "La Liga", "Liga Profesional", "Liga MX"}),
}

# 2-4 rende meglio di 5: un 5 si riconosce alla seconda tappa e il video non regge.
POPULARITY_SCORE = {1: 0.5, 2: 3.0, 3: 3.2, 4: 2.8, 5: 1.0}

# (min, max) tappe, prima nella versione stretta poi in quella allargata se non c'e' niente.
LENGTHS = {
    "who_is": ((6, 12), (4, 14)),
    "percent": ((6, 12), (4, 14)),
    "solution": ((2, 20), (2, 20)),
    "journeyman": ((10, 20), (10, 24)),
    "ladder": ((4, 9), (3, 11)),
}

# Sotto questa soglia la percentuale non dice niente ("il 100% l'ha indovinato" su 2 persone).
MIN_PLAYERS_FOR_PERCENT = 5

LADDER_BANDS = ("easy", "medium", "hard", "impossible")

DEFAULT_WINDOW_DAYS = 120


class SpoilerError(ValueError):
    """La scheda verrebbe da una sfida non ancora chiusa o dal dataset non riservato."""


class NoMaterial(LookupError):
    """Nessun percorso adatto al formato con i vincoli dati."""


# ---------------------------------------------------------------------------------------
# Regola anti-spoiler
# ---------------------------------------------------------------------------------------

def assert_safe(card: Card, game: GameSource, today: Optional[str] = None) -> Card:
    today = today or game.today()
    if card.source_key.startswith(DAY_PREFIX):
        day = card.source_day or ""
        if not day or not day < today:
            raise SpoilerError(f"la sfida del {day} non e' ancora chiusa (oggi e' {today})")
        return card
    if card.source_key.startswith(POOL_PREFIX):
        player = game.player(card.source_key[len(POOL_PREFIX):])
        if not player or not player.get("practice_only"):
            raise SpoilerError(f"{card.player_id} non e' nel pool riservato: potrebbe uscire come sfida")
        return card
    raise SpoilerError(f"sorgente sconosciuta: {card.source_key!r}")


# ---------------------------------------------------------------------------------------
# Costruzione delle schede
# ---------------------------------------------------------------------------------------

def _display_answer(answers) -> str:
    full = [a for a in answers or () if " " in a]
    if full:
        return max(full, key=len).title()
    return (list(answers or ()) or ["?"])[0].title()


def _stops(game: GameSource, career: list, lang: str) -> tuple:
    ordered = game.order_career(list(career or ()))
    localized = game.localize_career(ordered, lang)
    return tuple(
        Stop(
            team=stop.get("team") or "?",
            years=game.years_label(stop),
            league=stop.get("league") or "",
            country=stop.get("country") or "",
        )
        for stop in localized
    )


def percent_solved(players_count, solved_count) -> Optional[int]:
    players = int(players_count or 0)
    if players <= 0:
        return None
    return max(0, min(100, round(100 * int(solved_count or 0) / players)))


def card_from_challenge(game: GameSource, day: str, doc: dict, lang: str) -> Card:
    player = game.player(doc.get("player_id")) or {}
    return Card(
        player_id=doc.get("player_id") or "",
        player_name=player.get("full_name") or _display_answer(doc.get("correct_answers")),
        stops=_stops(game, doc.get("career_path") or [], lang),
        source_key=DAY_PREFIX + day,
        popularity=player.get("popularity"),
        percent_solved=percent_solved(doc.get("players_count"), doc.get("solved_count")),
        players_count=int(doc.get("players_count") or 0),
        difficulty=doc.get("difficulty") or (game.difficulty(player) if player else None),
    )


def card_from_pool(game: GameSource, player: dict, lang: str) -> Card:
    return Card(
        player_id=player["id"],
        player_name=player.get("full_name") or player["id"],
        stops=_stops(game, player.get("career") or [], lang),
        source_key=POOL_PREFIX + player["id"],
        popularity=player.get("popularity"),
        percent_solved=None,
        players_count=0,
        difficulty=game.difficulty(player),
    )


def _playable(doc) -> bool:
    return bool(doc and doc.get("career_path") and doc.get("correct_answers"))


def check_format(card: Card, fmt: str) -> Card:
    """Una scheda scelta a mano (`--day`, `--pool-player`) deve comunque stare nel formato:
    37 tappe in un `who_is` farebbero un video fuori durata e illeggibile."""
    if fmt == "ladder":
        return card
    low, high = LENGTHS[fmt][1]
    if not low <= len(card.stops) <= high:
        hint = " (prova journeyman)" if len(card.stops) > high and fmt != "journeyman" else ""
        raise NoMaterial(f"{len(card.stops)} tappe non vanno bene per {fmt}: servono {low}-{high}{hint}")
    if fmt == "percent" and card.percent_solved is None:
        raise NoMaterial("percent richiede una giornata con giocatori registrati (players_count > 0)")
    return card


def card_for_day(game: GameSource, day: str, lang: str) -> Card:
    """La scheda di una giornata precisa (`--day`). Solo se e' gia' chiusa."""
    today = game.today()
    if not day < today:
        raise SpoilerError(f"la sfida del {day} non e' ancora chiusa (oggi e' {today}): niente spoiler")
    doc = game.challenge(day)
    if not _playable(doc):
        raise NoMaterial(f"nessuna sfida utilizzabile il {day}")
    return assert_safe(card_from_challenge(game, day, doc, lang), game, today)


def card_for_pool_player(game: GameSource, player_id: str, lang: str) -> Card:
    player = game.player(player_id)
    if not player or not player.get("practice_only"):
        raise SpoilerError(f"{player_id} non e' nel pool riservato (practice_only)")
    return assert_safe(card_from_pool(game, player, lang), game)


# ---------------------------------------------------------------------------------------
# Selezione
# ---------------------------------------------------------------------------------------

def candidates(game: GameSource, lang: str, window_days: int = DEFAULT_WINDOW_DAYS,
               include_pool: bool = True) -> list:
    """Tutte le schede sicure: sfide chiuse nella finestra + pool riservato."""
    today = game.today()
    cards = []
    for doc in game.past_challenges(before_day=today, limit=window_days):
        day = doc.get("day") or ""
        # Doppio controllo: la query dovrebbe gia' escludere oggi e il futuro, ma una
        # sorgente sbagliata qui non deve bastare a fare uno spoiler.
        if not day or not day < today or not _playable(doc):
            continue
        cards.append(card_from_challenge(game, day, doc, lang))
    if include_pool:
        for player in game.reserved_players():
            if player.get("practice_only"):
                cards.append(card_from_pool(game, player, lang))
    return cards


def score(card: Card, fmt: str, lang: str, relaxed: bool = False) -> Optional[float]:
    """Punteggio del percorso per il formato, o None se non e' adatto."""
    low, high = LENGTHS[fmt][1 if relaxed else 0]
    n = len(card.stops)
    if not low <= n <= high:
        return None
    if fmt == "percent" and (card.percent_solved is None or card.players_count < MIN_PLAYERS_FOR_PERCENT):
        return None

    value = POPULARITY_SCORE.get(card.popularity or 0, 1.5)
    if 6 <= n <= 12:
        value += 1.0
    preferred = PREFERRED_LEAGUES.get(lang)
    if preferred and any(stop.league in preferred for stop in card.stops):
        value += 1.5
    if card.source_day:
        value += 0.5  # le sfide vere hanno i numeri e un "sapore" diverso dal pool
    if fmt == "percent":
        # "Solo il 12%" aggancia piu' di "il 70%".
        value += (100 - card.percent_solved) / 40
    if fmt == "journeyman":
        value += min(n - 10, 6) * 0.3
    return value


def pick(game: GameSource, fmt: str, lang: str, *, seed: str = "", window_days: int = DEFAULT_WINDOW_DAYS,
         exclude_player_ids: Iterable[str] = (), exclude_source_keys: Iterable[str] = (),
         pool: Optional[list] = None) -> Card:
    """La scheda migliore per il formato, deterministica a parita' di `seed` e di dati."""
    return pick_many(game, fmt, lang, 1, seed=seed, window_days=window_days,
                     exclude_player_ids=exclude_player_ids, exclude_source_keys=exclude_source_keys,
                     pool=pool)[0]


def pick_many(game: GameSource, fmt: str, lang: str, count: int, *, seed: str = "",
              window_days: int = DEFAULT_WINDOW_DAYS, exclude_player_ids: Iterable[str] = (),
              exclude_source_keys: Iterable[str] = (), pool: Optional[list] = None) -> list:
    today = game.today()
    excluded_players = set(exclude_player_ids or ())
    excluded_keys = set(exclude_source_keys or ())
    material = pool if pool is not None else candidates(game, lang, window_days)
    material = [c for c in material if c.player_id not in excluded_players and c.source_key not in excluded_keys]

    rng = random.Random(f"{seed}|{fmt}|{lang}")
    for relaxed in (False, True):
        ranked = []
        for card in sorted(material, key=lambda c: c.source_key):
            value = score(card, fmt, lang, relaxed)
            if value is not None:
                ranked.append((value + rng.random() * 0.8, card))
        ranked.sort(key=lambda item: (-item[0], item[1].source_key))
        chosen, seen = [], set()
        for _, card in ranked:
            if card.player_id in seen:
                continue
            seen.add(card.player_id)
            chosen.append(assert_safe(card, game, today))
            if len(chosen) == count:
                return chosen
    raise NoMaterial(f"nessun percorso adatto al formato {fmt} ({lang})")


def _band(card: Card) -> str:
    if card.difficulty in LADDER_BANDS:
        return card.difficulty
    pop = card.popularity or 3
    return {5: "easy", 4: "medium", 3: "hard"}.get(pop, "impossible")


def pick_ladder(game: GameSource, lang: str, *, seed: str = "", window_days: int = DEFAULT_WINDOW_DAYS,
                exclude_player_ids: Iterable[str] = (), pool: Optional[list] = None) -> list:
    """Quattro percorsi, uno per fascia, da facile a impossibile.

    Qui un 5 di popolarita' e' benvenuto: e' il gradino "facile" che fa sentire bravo chi
    guarda, prima degli altri tre."""
    today = game.today()
    excluded = set(exclude_player_ids or ())
    material = pool if pool is not None else candidates(game, lang, window_days)
    material = [c for c in material if c.player_id not in excluded]
    rng = random.Random(f"{seed}|ladder|{lang}")
    chosen, used = [], set()
    for band in LADDER_BANDS:
        best = None
        for relaxed in (False, True):
            low, high = LENGTHS["ladder"][1 if relaxed else 0]
            options = sorted(
                (c for c in material if _band(c) == band and c.player_id not in used and low <= len(c.stops) <= high),
                key=lambda c: c.source_key,
            )
            if options:
                best = rng.choice(options)
                break
        if best is None:
            raise NoMaterial(f"nessun percorso per la fascia {band} della scala ({lang})")
        used.add(best.player_id)
        chosen.append(assert_safe(best, game, today))
    return chosen
