"""Un gioco finto: stesse risposte di promo.game.GameRepo, senza Firestore ne' rete."""
import colorsys
import hashlib

from promo.game import DEFAULT_PALETTE

TODAY = "2026-09-24"


def career(*teams, start=2000, league="Serie A", country="Italia"):
    stops = []
    year = start
    for team in teams:
        if isinstance(team, tuple):
            team, league_ = team
        else:
            league_ = league
        stops.append({"team": team, "country": country, "league": league_, "start_year": year, "end_year": year + 2})
        year += 2
    return stops


def player(pid, name, teams, popularity=3, practice_only=False, **kw):
    return {
        "id": pid,
        "full_name": name,
        "aliases": [name.lower()],
        "popularity": popularity,
        "career": career(*teams, **kw),
        "practice_only": practice_only,
        "verified": True,
    }


def challenge(day, p, players_count=0, solved_count=0, difficulty=None):
    return {
        "day": day,
        "player_id": p["id"],
        "career_path": p["career"],
        "correct_answers": [p["full_name"].lower()],
        "players_count": players_count,
        "solved_count": solved_count,
        "difficulty": difficulty,
    }


TEAMS = ["Malmö FF", "Ajax", "Juventus", "Inter", "Barcelona", "AC Milan", "Paris Saint-Germain",
         "Manchester United", "LA Galaxy", "Genoa", "Torino", "Napoli", "Lazio", "Roma", "Sevilla",
         "Valencia", "Porto", "Benfica", "Celtic", "Rangers", "Monaco", "Lyon", "Marseille", "Lille"]


def default_players():
    return [
        player("zlatan", "Zlatan Ibrahimović", TEAMS[:10], popularity=5),
        player("mid", "Mario Medio", TEAMS[2:10], popularity=3),
        player("pl", "Peter League", [("Chelsea", "Premier League"), ("Arsenal", "Premier League")] + TEAMS[3:8], popularity=3),
        player("nomad", "Nick Nomad", TEAMS[:14], popularity=2),
        player("future", "Futuro Spoiler", TEAMS[4:12], popularity=3),
        player("unused", "Mai Usato", TEAMS[5:13], popularity=3),
        player("reserved", "Riccardo Riservato", TEAMS[6:14], popularity=3, practice_only=True),
        player("easy5", "Easy Five", TEAMS[1:6], popularity=5),
        player("hard2", "Hard Two", TEAMS[8:14], popularity=2),
        player("imp1", "Impossible One", TEAMS[12:18], popularity=1),
    ]


class FakeGame:
    def __init__(self, today=TODAY, players=None, challenges=None, hogql_rows=None):
        self._today = today
        self.players = {p["id"]: p for p in (default_players() if players is None else players)}
        if challenges is None:
            p = self.players
            challenges = [
                challenge("2026-09-10", p["zlatan"], 40, 12, "easy"),
                challenge("2026-09-20", p["mid"], 30, 6, "medium"),
                challenge("2026-09-21", p["pl"], 0, 0, "hard"),
                challenge("2026-09-22", p["nomad"], 20, 3, "impossible"),
                challenge("2026-09-23", p["easy5"], 50, 45, "easy"),
                challenge("2026-09-19", p["hard2"], 10, 2, "hard"),
                challenge("2026-09-18", p["imp1"], 10, 1, "impossible"),
                # Oggi e domani: non devono uscire mai.
                challenge(today, p["future"], 5, 1, "medium"),
                challenge("2026-09-25", p["unused"], 0, 0, "medium"),
            ]
        self.challenges = {c["day"]: c for c in challenges}
        self.hogql_rows = hogql_rows or {}
        self.queries = []

    def today(self):
        return self._today

    def past_challenges(self, before_day, limit):
        # Volutamente "sbagliato": restituisce anche oggi e il futuro, per verificare che la
        # regola anti-spoiler non si fidi della query.
        docs = sorted(self.challenges.values(), key=lambda c: c["day"], reverse=True)
        return docs[:limit]

    def challenge(self, day):
        return self.challenges.get(day)

    def reserved_players(self):
        # Anche qui sbagliato di proposito: restituisce tutto il dataset.
        return list(self.players.values())

    def player(self, player_id):
        return self.players.get(player_id)

    def difficulty(self, player):
        return {5: "easy", 4: "medium", 3: "hard"}.get(player.get("popularity"), "impossible")

    def order_career(self, career):
        return sorted(career, key=lambda s: s.get("start_year") or 0)

    def localize_career(self, career, lang):
        names = {"Italia": {"en": "Italy", "es": "Italia"}}
        return [dict(s, country=names.get(s.get("country"), {}).get(lang, s.get("country"))) for s in career]

    def years_label(self, stop):
        end = stop.get("end_year")
        return f"{stop.get('start_year')} – {end}" if end else f"{stop.get('start_year')} – …"

    def team_color(self, team):
        digest = hashlib.md5(team.encode("utf-8")).hexdigest()
        r, g, b = colorsys.hsv_to_rgb(int(digest[:4], 16) / 65535, 0.58, 0.88)
        return (int(r * 255), int(g * 255), int(b * 255))

    def palette(self):
        return dict(DEFAULT_PALETTE)

    def title_font_path(self):
        return None

    def text_font_path(self):
        return None

    def campaign_sources(self):
        return ("tiktok", "instagram", "youtube", "reddit", "x", "threads", "facebook",
                "telegram_group", "creator", "producthunt", "directory", "qr")

    def hogql(self, query):
        self.queries.append(query)
        for marker, rows in self.hogql_rows.items():
            if marker in query:
                return rows
        return []

    def firestore_db(self):
        raise RuntimeError("niente Firestore nei test")
