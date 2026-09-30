"""Il webhook delle approvazioni: Telegram lo chiama appena l'admin preme ✅ o ❌.

Gira su Cloud Run (vedi `Dockerfile` e docs/promo-studio.md, "Approvazione immediata"),
separato dal gioco e senza il suo repository: gli servono solo Firestore e il bot di
approvazione. La decisione passa da `approvals.handle_press`, la stessa di `sync`, con gli
stessi controlli (solo l'admin, solo post ancora in bozza). I pulsanti dei brief del supervisore
(`brief:use:<campaign_id>` / `brief:skip:<campaign_id>`) seguono la stessa strada e finiscono in
`promo_brief_decisions` (promo/supervisor_briefs.py): al servizio non serve il Firestore del supervisore.

Il servizio e' pubblico perche' Telegram non sa autenticarsi con Google: lo protegge il
segreto che Telegram rimanda in `X-Telegram-Bot-Api-Secret-Token` (impostato con
`python -m promo approval-webhook --set`). Senza segreto configurato rifiuta tutto.

Su `/dispatch?command=...` avvia anche i lavori programmati per conto di Cloud Scheduler
(promo/dispatch.py): li' vale solo un token OIDC di Google del service account di Scheduler.

    gunicorn --bind :$PORT --no-control-socket "promo.approval_service:create_app()"
"""
import hmac
import json
from threading import Lock
from typing import Callable, Optional
from urllib.parse import parse_qs

from promo import approvals, config, dispatch, log, supervisor_briefs
from promo.store import FirestoreStore

SECRET_HEADER = "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"
MAX_BODY = 64 * 1024


def _reply(start_response, status: str, body: str = ""):
    data = body.encode("utf-8")
    start_response(status, [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(data)))])
    return [data]


def make_app(settings, store_factory: Callable, bot_factory: Callable = approvals.build_bot, *,
             verify_token: Optional[Callable] = None, github_session=None,
             decisions_factory: Callable = supervisor_briefs.decisions_for):
    """L'app WSGI. Store, decisioni sui brief e bot si creano alla prima richiesta, e una volta sola.
    `decisions_factory` riceve la coda e ritorna lo store di `promo_brief_decisions` (o None)."""
    state, lock = {}, Lock()

    def start(environ, start_response):
        """Cloud Scheduler: /dispatch?command=drafts|publish|report."""
        if environ.get("REQUEST_METHOD") != "POST":
            return _reply(start_response, "405 Method Not Allowed")
        if not dispatch.verify_google_caller(environ.get("HTTP_AUTHORIZATION", ""), settings.dispatch_audience,
                                             settings.dispatch_invoker, verify_token):
            log.warning("dispatch: chiamata senza un'identita' Google valida, rifiutata")
            return _reply(start_response, "403 Forbidden")
        command = parse_qs(environ.get("QUERY_STRING", "")).get("command", [""])[0]
        try:
            dispatch.start_workflow(command, settings.github_dispatch_token, github_session)
        except ValueError as e:
            return _reply(start_response, "400 Bad Request", str(e))
        except Exception as e:  # Cloud Scheduler riprova
            log.warning("dispatch: %s non avviato (%s)", command, log.scrub(e))
            return _reply(start_response, "502 Bad Gateway")
        log.info("dispatch: %s avviato su GitHub", command)
        return _reply(start_response, "200 OK", f"{command} avviato")

    def deps():
        with lock:
            if not state:
                state["store"] = store_factory()
                state["decisions"] = decisions_factory(state["store"])
                state["bot"] = bot_factory(settings)
        return state["store"], state["bot"], state["decisions"]

    def app(environ, start_response):
        if environ.get("PATH_INFO") == "/dispatch":
            return start(environ, start_response)
        if environ.get("REQUEST_METHOD") == "GET":
            return _reply(start_response, "200 OK", "ok")  # controllo di salute di Cloud Run
        if environ.get("REQUEST_METHOD") != "POST":
            return _reply(start_response, "405 Method Not Allowed")
        secret = settings.approval_webhook_secret
        given = environ.get(SECRET_HEADER, "")
        if not secret or not hmac.compare_digest(given.encode("utf-8"), secret.encode("utf-8")):
            log.warning("webhook: richiesta senza il segreto giusto, rifiutata")
            return _reply(start_response, "403 Forbidden")
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return _reply(start_response, "400 Bad Request")
        try:
            update = json.loads(environ["wsgi.input"].read(length))
        except (ValueError, UnicodeDecodeError):
            return _reply(start_response, "400 Bad Request")

        callback = update.get("callback_query") if isinstance(update, dict) else None
        if not callback:
            return _reply(start_response, "200 OK")  # altro che pulsanti: niente da fare
        store, bot, decisions = deps()
        if bot is None:
            log.warning("webhook: mancano PROMO_APPROVAL_BOT_TOKEN o PROMO_ADMIN_CHAT_ID")
            return _reply(start_response, "503 Service Unavailable")
        try:
            for line in approvals.handle_press(store, bot, settings, callback, decisions):
                log.info("webhook: %s", line)
        except Exception as e:  # Telegram riprova: handle_press e' idempotente
            log.warning("webhook: pulsante non applicato (%s)", log.scrub(e))
            return _reply(start_response, "500 Internal Server Error")
        return _reply(start_response, "200 OK")

    return app


def firestore_store() -> FirestoreStore:
    """Firestore del progetto in cui gira il servizio, con l'identita' di Cloud Run."""
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.ApplicationDefault())
    return FirestoreStore(firestore.client())


def create_app():
    log.configure()
    settings = config.load()
    log.register_secrets(settings.secret_values())
    return make_app(settings, firestore_store)
