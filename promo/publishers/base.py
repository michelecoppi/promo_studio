"""Interfaccia comune dei publisher (§5.5): `publish(post, media_path) -> PublishResult`.

Un publisher non solleva eccezioni per gli errori del canale: li restituisce in
`PublishResult.error`, e promo/plan.py porta il post a `failed` con il motivo. Le
eccezioni impreviste vengono comunque intercettate da plan.py: il batch non si ferma mai.

La rete passa da `session` (un `requests.Session` o un finto nei test): nessuna chiamata
di rete nei test.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PublishResult:
    ok: bool
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: str = ""

    @classmethod
    def failure(cls, error: str) -> "PublishResult":
        return cls(ok=False, error=error)


class Publisher:
    channel = ""

    def publish(self, post: dict, media_path: str) -> PublishResult:  # pragma: no cover - interfaccia
        raise NotImplementedError

    def describe(self, post: dict, media_path: str) -> str:
        """Cosa farebbe `publish`, per --dry-run. Nessuna chiamata esterna."""
        return f"pubblicherebbe {media_path} su {self.channel}"


def post_text(post: dict) -> str:
    """Didascalia + hashtag + link tracciato, per i canali che mostrano il testo."""
    hashtags = " ".join(post.get("hashtags") or [])
    parts = [post.get("caption", "").strip(), hashtags, post.get("tracking_link", "")]
    return "\n\n".join(part for part in parts if part)
