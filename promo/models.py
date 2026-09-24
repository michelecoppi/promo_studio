"""Le forme dei dati: la scheda di un contenuto e il post in coda.

`Card` e' tutto quello che serve per disegnare un video e scriverne i testi, gia' ordinato
e tradotto: il renderer non legge Firestore ne' il dataset. Il post salva la scheda dentro
`render_spec`, cosi' il video si puo' rigenerare identico (il rendering e' deterministico)
su un'altra macchina - il workflow delle 12:00 non ha il file prodotto alle 07:00.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional

FORMATS = ("who_is", "percent", "ladder", "journeyman", "solution")
CHANNELS = ("tiktok", "telegram_channel", "x")

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED, STATUS_PUBLISHED, STATUS_FAILED)

POOL_PREFIX = "pool:"
DAY_PREFIX = "day:"


@dataclass(frozen=True)
class Stop:
    team: str
    years: str
    league: str = ""
    country: str = ""


@dataclass(frozen=True)
class Card:
    """Un percorso pronto da mostrare.

    `source_key` usa la stessa convenzione di services/practice_content.py (`day:<ISO>` o
    `pool:<id>`). `percent_solved` e' None quando nessuno ha giocato quella giornata o la
    giornata e' precedente ai contatori: in quel caso il numero non si mostra, mai inventato."""
    player_id: str
    player_name: str
    stops: tuple
    source_key: str
    popularity: Optional[int] = None
    percent_solved: Optional[int] = None
    players_count: int = 0
    difficulty: Optional[str] = None

    @property
    def source_day(self) -> Optional[str]:
        return self.source_key[len(DAY_PREFIX):] if self.source_key.startswith(DAY_PREFIX) else None

    @property
    def origin(self) -> str:
        """La parte `<giorno_origine>` dell'id del post: la data o `pool-<id>`."""
        if self.source_day:
            return self.source_day
        return "pool-" + self.source_key[len(POOL_PREFIX):]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["stops"] = [asdict(stop) for stop in self.stops]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Card":
        data = dict(data)
        data["stops"] = tuple(Stop(**stop) for stop in data.get("stops", ()))
        return cls(**data)


@dataclass
class Copy:
    caption: str
    hashtags: list
    pinned_comment: str
    tracking_link: str

    def caption_with_hashtags(self) -> str:
        return (self.caption + " " + " ".join(self.hashtags)).strip()


@dataclass
class RenderResult:
    video_path: str
    cover_path: str
    duration: float
    sha256: str = ""
    extra: dict = field(default_factory=dict)


def post_id(origin: str, fmt: str, language: str, channel: str) -> str:
    """Deterministico (§6): rigenerare le bozze dello stesso giorno non crea doppioni."""
    return f"{origin}-{fmt}-{language}-{channel}"
