"""Avvio dei lavori programmati da Cloud Scheduler, al posto dei cron di GitHub.

I cron di GitHub Actions sono "best effort": nelle ore di punta partono in ritardo, o non
partono affatto. Cloud Scheduler invece parte all'ora esatta (e conosce l'ora legale di
Roma), ma non sa avviare un workflow di GitHub con un token tenuto in Secret Manager. Quindi:

    Cloud Scheduler --(identita' Google, OIDC)--> promo-approvals /dispatch?command=drafts
                    --(token GitHub da Secret Manager)--> workflow_dispatch di promo.yml

Il servizio accetta solo le chiamate firmate da Google per il service account di Cloud
Scheduler (`PROMO_DISPATCH_INVOKER`) e per il proprio indirizzo (`PROMO_DISPATCH_AUDIENCE`),
e avvia solo i comandi in `COMMANDS`, mai in `--dry-run`. I lavori sono idempotenti: se anche
un cron di GitHub parte, un doppio avvio non fa danni.
"""
from typing import Callable, Optional

import requests

REPO = "michelecoppi/promo_studio"
WORKFLOW = "promo.yml"
REF = "main"
COMMANDS = frozenset({"drafts", "publish", "report"})
TIMEOUT = 30


class DispatchError(RuntimeError):
    """GitHub non ha accettato l'avvio del workflow."""


def verify_google_caller(authorization: str, audience: str, invoker: str,
                         verify: Optional[Callable] = None) -> bool:
    """Vero solo per un token OIDC di Google, per questo servizio, emesso a `invoker`."""
    if not (audience and invoker and authorization.startswith("Bearer ")):
        return False
    token = authorization[len("Bearer "):].strip()
    if verify is None:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        def verify(tok, aud):
            return id_token.verify_oauth2_token(tok, google_requests.Request(), audience=aud)
    try:
        claims = verify(token, audience)
    except ValueError:  # firma, scadenza o audience sbagliate
        return False
    return bool(claims.get("email_verified")) and claims.get("email") == invoker


def start_workflow(command: str, token: str, session=None) -> None:
    """Avvia promo.yml su GitHub con `command`, sul serio (dry_run=false)."""
    if command not in COMMANDS:
        raise ValueError(f"comando non ammesso: {command!r} ({', '.join(sorted(COMMANDS))})")
    if not token:
        raise DispatchError("manca PROMO_GITHUB_DISPATCH_TOKEN")
    response = (session or requests).post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        json={"ref": REF, "inputs": {"command": command, "dry_run": "false"}},
        timeout=TIMEOUT,
    )
    if response.status_code != 204:
        raise DispatchError(f"GitHub ha risposto {response.status_code}")
