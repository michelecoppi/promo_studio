# Promo Studio

Documento primario del Promo Studio: cosa fa, come si configura, come si approva, come si spegne.
Specifica di riferimento: *Promo Studio — Guess the Player from the Path* (2026-09-24).

**Principio: la macchina prepara, la persona approva.** Tutto ciò che è pubblico o costa denaro passa da
un'approvazione umana esplicita. Lo strumento non spende, non scrive a persone o gruppi di terzi e non
pubblica niente che non sia `approved`.

## Indice

- [Architettura](#architettura)
- [Installazione](#installazione)
- [Uso quotidiano](#uso-quotidiano)
- [Formati video](#formati-video)
- [Regola anti-spoiler](#regola-anti-spoiler)
- [Dashboard](#dashboard)
- [Coda e approvazione](#coda-e-approvazione)
- [Brief del supervisore](#brief-del-supervisore)
- [Pubblicazione](#pubblicazione)
- [Report settimanale](#report-settimanale)
- [Pianificazione (GitHub Actions)](#pianificazione-github-actions)
- [Configurazione](#configurazione)
- [Come si spegne](#come-si-spegne)
- [Modifiche richieste nel repository del gioco](#modifiche-richieste-nel-repository-del-gioco)
- [Stato delle milestone](#stato-delle-milestone)

## Architettura

Il Promo Studio vive in un repository suo (`michelecoppi/promo_studio`) ma **riusa** il codice del gioco
invece di riscriverlo. L'unico punto di contatto è `promo/game.py`, che importa da una checkout del gioco
indicata da `GAME_REPO_PATH`:

| Dal gioco | Uso |
| --- | --- |
| `services/firebase_service.py` (`get_past_daily_paths`, `get_daily_path`, `db`) | sfide passate, statistiche, coda `promo_posts` |
| `services/player_pool.py` (`get_practice_players`, `get_player_by_id`) | pool riservato, popolarità, nomi |
| `services/career_order.py`, `services/content_i18n.py::localize_career` | ordine e traduzione delle tappe |
| `services/difficulty.py::compute_difficulty` | fasce della scala (`ladder`) |
| `services/path_image.py` (`color_for_team`, `years_label`, palette) | coerenza grafica con il bot |
| `webapp/src/assets/fonts/BarlowCondensed-SemiBold.woff2` (o `.ttf`, se c'è), `services/fonts.py` | font dei titoli e di ripiego |
| `services/product_analytics_query.py` (`run_hogql`, `QueryError`), `services/product_analytics.py::CAMPAIGN_SOURCES` | report e link `src_` |
| `services/observability.py::scrub_text` | log senza segreti |

Il resto del pacchetto non importa mai `services.*`: i test girano con un gioco finto (`tests/fakes.py`),
senza Firestore e senza rete. Se un giorno il Promo Studio entrerà nel repository del gioco come
`apps/promo/`, basta sostituire `promo/game.py` con import diretti.

```
promo/
  game.py          confine verso il gioco
  picker.py        selezione dei percorsi + regola anti-spoiler
  render/          motore video (Pillow + ffmpeg) e formati
  catalog/*.json   testi IT/EN/ES (video e didascalie)
  copy.py          didascalie, hashtag, commento fissato, link tracciato
  store.py         coda promo_posts (Firestore | file JSON | memoria)
  queue.py         macchina a stati e azioni dell'admin
  plan.py          bozze quotidiane e pubblicazione
  publishers/      telegram.py, tiktok.py, x.py (stub)
  report.py        report settimanale
  briefs.py        brief-import: brief del supervisore → bozza
  supervisor_briefs.py  brief letti dal Firestore del supervisore, ✅ Usa / ❌ Scarta sul bot
  cli.py           python -m promo <comando>
admin/             pagina Streamlit di approvazione
```

Non gira nel servizio Cloud Run del bot: gira sulla macchina del maintainer o nel workflow
`.github/workflows/promo.yml`. Il `Dockerfile` del bot non cambia.

## Installazione

```bash
git clone https://github.com/michelecoppi/promo_studio && cd promo_studio
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # + requirements-admin.txt per la pagina admin
export GAME_REPO_PATH=../guess_the_player_from_the_path
pip install -r "$GAME_REPO_PATH/requirements.txt"
cp .env.example .env                          # e compila i valori
python -m promo doctor
```

ffmpeg arriva con `imageio-ffmpeg` (binario separato, invocato come processo: nessun impatto sulla
licenza PolyForm Noncommercial). `PROMO_FFMPEG` permette di usarne un altro.

## Uso quotidiano

```bash
# M1: un video a mano, da una giornata già chiusa (o dal pool riservato)
python -m promo render --format who_is --lang it --day 2026-09-10
python -m promo render --format journeyman --lang es --pool-player mauro_zarate --check-layout
# → out/<origine>-<formato>-<lingua>.mp4, .png (copertina), .txt (didascalia, hashtag, commento), .json

python -m promo drafts                 # bozze del giorno in coda (serve PROMO_ENABLED=true)
python -m promo brief-import brief.json  # bozza da un brief del supervisore (vedi sotto)
streamlit run admin/app.py             # approva / rifiuta / modifica la didascalia
python -m promo list --status draft
python -m promo approve <id> --by michele
python -m promo publish --dry-run      # cosa uscirebbe, senza chiamare nessuno
python -m promo publish
python -m promo report                 # reports/promo-report-<inizio>.md
python -m promo brief --lang it        # testo per un creator pagato, con #adv (da copiare a mano)
```

Senza Firestore si può provare tutto con `PROMO_OFFLINE=true` e `PROMO_STORE=local` (solo pool riservato,
coda in `promo_posts.json`): vedi [`demo-setup.md`](demo-setup.md). `publish --id <id> --now` pubblica
subito un singolo post approvato, senza aspettare le 12:00.

## Formati video

1080×1920, 30 fps, H.264 + AAC, effetti sonori sintetizzati (tic per tappa, bip del conto alla rovescia,
nessuna musica: l'audio di tendenza lo aggiunge la persona su TikTok). Copertina PNG dal primo
fotogramma utile. Output deterministico: stessi input → stesso file byte per byte (ffmpeg `bitexact`,
nessun metadato). Tempo di generazione misurato: 12-15 s per un video di 20-25 s.

| Formato | Contenuto | Durata |
| --- | --- | --- |
| `who_is` | aggancio 2 s → tappe una alla volta (~1,1 s, "Tappa n di N") → pausa sull'ultima → "Hai 3 tentativi" 3-2-1 → chiusura | 18-25 s |
| `percent` | come `who_is`, aggancio con la percentuale **vera** (solo se ≥ 5 giocatori) | 18-25 s |
| `journeyman` | 10+ club, tappe più rapide (~0,75 s) | 20-30 s |
| `ladder` | quattro percorsi (facile, medio, difficile, impossibile), ~5,5 s ciascuno | 25-30 s |
| `solution` | il giorno dopo: stesso percorso + nome rivelato (+ percentuale se nota) | 8-12 s |

**Zona sicura.** Nessun testo negli ultimi 170 px a destra né negli ultimi 250 px in basso.
`engine.check_layout()` lo verifica sui fotogrammi (`--check-layout`, e in CI per ogni formato e lingua).

Rispetto al prototipo `make.py`: pausa più lunga sull'ultima tappa, percentuale reale, tappe che entrano
dal basso (da destra passavano sotto i pulsanti di TikTok), freccia del prestito disegnata con un font
che ha il glifo, durata adattata al numero di tappe.

Nessuno stemma, logo, foto o musica: solo nomi dei club e colori generati (`color_for_team`).

## Regola anti-spoiler

Si usano **solo** sfide già chiuse (`day < oggi` nel fuso Europe/Rome) o il pool riservato
(`practice_only: true`). Un giocatore del dataset non ancora usato non si sceglie mai.

La regola è applicata due volte: nella raccolta dei candidati e con `picker.assert_safe()` su ogni
scheda restituita (anche per `--day` e `--pool-player`). I test (`tests/test_picker.py`) usano un gioco
finto che restituisce *di proposito* anche la sfida di oggi, quella di domani e l'intero dataset come
"pool": nessuna di queste deve mai uscire.

Criteri di scelta: popolarità 2-4 preferita a 5, 6-12 tappe, per EN/ES percorsi con Premier League,
LaLiga, Liga Profesional (Argentina) o Liga MX; giocatori usati negli ultimi 30 giorni esclusi.

## Dashboard

`streamlit run admin/app.py` (dopo `pip install -r requirements-admin.txt`). Si apre anche se il gioco o
Firestore non sono raggiungibili, e in quel caso dice cosa manca. Schede:

| Scheda | Cosa c'è |
| --- | --- |
| 🏠 **Stato** | interruttore acceso/spento, numeri della coda, **cosa aspetta te** (bozze scadute, pubblicazioni fallite con il motivo), prossimi lavori pianificati con il conto alla rovescia, prossime uscite approvate, controlli di configurazione con le istruzioni per sistemarli, attività recente |
| 📝 **Coda** | filtri per giorno, lingua, canale, stato; per ogni post anteprima video (o rigenerazione), didascalia/hashtag/commento modificabili, **Approva**, **Rifiuta** con motivo, storico; "approva tutte le bozze visibili" |
| 🎬 **Genera** | un video a mano (formato, lingua, scelta automatica / giornata chiusa / pool riservato), anteprima, testi da copiare, download MP4, **metti in coda come bozza** |
| 📤 **Pubblica** | cosa esce alla prossima pubblicazione e più tardi, **Simula (dry-run)**, **Pubblica ora** con conferma |
| 📊 **Pubblicati e report** | contenuti usciti (grafico 30 giorni, link), report settimanale (generazione e archivio), **editor dei costi** per canale |
| 📖 **Guida** | come funziona, configurazione passo per passo con lo stato di ogni passo, comandi utili |

`python -m promo doctor` mostra gli stessi controlli della scheda Stato (`promo/status.py`).

## Coda e approvazione

Ogni contenuto è un documento `promo_posts/{id}` con id deterministico
`<giorno_origine>-<formato>-<lingua>-<canale>` (per il pool `pool-<id_giocatore>`, per la scala il giorno
di uscita).

```
draft ──→ approved ──→ published
  │          │  └────→ failed ──→ published   (riprovabile, max 3 tentativi)
  └──→ rejected ←──────┴──────────┘
```

- Approvare, rifiutare e modificare i testi richiedono il nome di una persona (`--by` o
  `PROMO_ADMIN_NAME`, oppure il campo nella barra laterale dell'admin); gli attori di sistema
  (`publisher`, `scheduler`, …) sono rifiutati.
- Solo il publisher porta un post a `published`/`failed`, e solo da `approved`/`failed`.
- Ogni cambio aggiunge una voce a `history` (`from`, `to`, `by`, `at`, `note`) oltre a `approved_at`,
  `approved_by`, `published_at`, `edited_by`, `edited_at`.

Campi (oltre a quelli della specifica §6): `created_for` (giorno di uscita), `cover_path`,
`media_sha256`, `duration`, `attempts`, `publishing_since` (lucchetto di pubblicazione), `history`,
`approval_message_id` (il messaggio Telegram con i pulsanti, vedi sotto),
`render_spec` (le schede usate, per rigenerare il video identico), `brief_id` e `brief` (solo per le
bozze nate da un [brief del supervisore](#brief-del-supervisore), altrimenti `null`). Le date sono
stringhe ISO 8601 UTC al secondo con `Z` (`store.now_iso`, es. `2026-09-24T10:30:00Z`).

**Contratto versionato:** [`docs/schemas/promo_post.v1.json`](schemas/promo_post.v1.json) (JSON Schema
2020-12) descrive stati, campi obbligatori e opzionali, tipi e formato delle date. Lo legge il supervisore
(gtp_orchestrator), che ne tiene una copia fissata a uno SHA e ci valida le sue fixture.
`tests/test_post_schema.py` valida contro lo schema i post veri di `make_post`, ogni transizione di `queue`,
la pubblicazione e `approval_message_id`: se si cambia un campo senza aggiornare lo schema, la CI fallisce.
Aggiungere un campo opzionale non richiede una nuova versione (`additionalProperties` è ammesso);
togliere o rinominare un campo, cambiarne il tipo o cambiare gli stati richiede `promo_post.v2.json` e
un'issue su `gtp_orchestrator`.

### Approvare da Telegram

Facoltativo, con un **bot dedicato** (creato con BotFather, diverso da quello del gioco) e la chat
privata dell'admin: `PROMO_APPROVAL_BOT_TOKEN` + `PROMO_ADMIN_CHAT_ID`. L'admin scrive `/start` al bot
una volta, poi:

1. dopo le bozze delle 08:37, `python -m promo ask-approval` gli manda ogni video in attesa con
   didascalia, canali e due pulsanti **✅ Approva** / **❌ Rifiuta**. Un video vale per tutti i canali
   della sua lingua (TikTok e canale Telegram);
2. il pulsante premuto porta i post in `approved`/`rejected` e aggiorna il messaggio
   ("✅ Approvato da …", pulsanti tolti). Con il [webhook](#approvazione-immediata-webhook-su-cloud-run)
   succede subito; senza, lo fa `python -m promo sync-approvals` (ogni mezz'ora fino alle 12:45 e subito
   prima di pubblicare), che legge i pulsanti con `getUpdates`;
3. dopo la pubblicazione l'admin riceve l'esito (link ai post o errori).

Il bot è separato perché quello del gioco riceve già gli aggiornamenti sul suo webhook. Contano solo i
pulsanti premuti dall'admin nella sua chat; un post già
deciso (anche dalla dashboard) non cambia. Chi approva risulta come `PROMO_ADMIN_NAME` o, se non c'è,
lo username Telegram. Per modificare la didascalia o approvare un solo canale resta la dashboard.

### Approvazione immediata (webhook su Cloud Run)

Senza webhook il pulsante aspetta il giro successivo di `sync-approvals`, che dipende dai cron di GitHub
(in ritardo o saltati nelle ore di punta). Con il webhook Telegram chiama subito un piccolo servizio,
`promo/approval_service.py`, che applica la decisione con la stessa logica di `sync` (`handle_press`)
e aggiorna il messaggio in pochi secondi. Con il webhook attivo `sync-approvals` si fa da parte da solo
(Telegram rifiuterebbe `getUpdates`), quindi il workflow non va toccato.

Il servizio è separato dal gioco e non usa il suo repository: nel container (`Dockerfile`) entrano solo
il pacchetto `promo` e `requirements-approvals.txt`; `.gcloudignore` tiene fuori `.env` e chiavi. È
pubblico perché Telegram non sa autenticarsi con Google: lo protegge il segreto che Telegram rimanda in
ogni chiamata (`X-Telegram-Bot-Api-Secret-Token`). Senza `PROMO_APPROVAL_WEBHOOK_SECRET` rifiuta tutto.

Una volta sola, dal progetto `guess-the-player-from-path-bot` (stessa regione del gioco):

```bash
PROJECT=guess-the-player-from-path-bot
SA=promo-approvals@$PROJECT.iam.gserviceaccount.com

# 1. identità del servizio: solo Firestore e i suoi due segreti
gcloud iam service-accounts create promo-approvals --project $PROJECT --display-name "Promo Studio: approvazioni"
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role roles/datastore.user

# 2. segreti: il token del bot di approvazione e un segreto casuale per il webhook
printf %s "<token del bot di approvazione>" | gcloud secrets create promo-approval-bot-token --project $PROJECT --data-file=-
python -c "import secrets; print(secrets.token_urlsafe(32), end='')" | gcloud secrets create promo-approval-webhook-secret --project $PROJECT --data-file=-
for s in promo-approval-bot-token promo-approval-webhook-secret; do
  gcloud secrets add-iam-policy-binding $s --project $PROJECT --member serviceAccount:$SA --role roles/secretmanager.secretAccessor
done

# 3. deploy
gcloud run deploy promo-approvals --project $PROJECT --region europe-west1 --source . \
  --service-account $SA --allow-unauthenticated --max-instances 1 --memory 256Mi \
  --set-env-vars PROMO_ADMIN_CHAT_ID=<chat id dell'admin> \
  --set-secrets PROMO_APPROVAL_BOT_TOKEN=promo-approval-bot-token:latest,PROMO_APPROVAL_WEBHOOK_SECRET=promo-approval-webhook-secret:latest

# 4. collega il bot al servizio (token e segreto letti da Secret Manager, mai scritti a mano)
export PROMO_ADMIN_CHAT_ID=<chat id dell'admin> \
  PROMO_APPROVAL_BOT_TOKEN=$(gcloud secrets versions access latest --secret promo-approval-bot-token --project $PROJECT) \
  PROMO_APPROVAL_WEBHOOK_SECRET=$(gcloud secrets versions access latest --secret promo-approval-webhook-secret --project $PROJECT)
python -m promo approval-webhook --set "$(gcloud run services describe promo-approvals --project $PROJECT --region europe-west1 --format 'value(status.url)')"
```

`python -m promo approval-webhook` mostra lo stato, `--delete` torna a `sync-approvals`. I pulsanti premuti
prima del collegamento non si perdono: Telegram li consegna al webhook appena collegato. Chi approva
risulta come `PROMO_ADMIN_NAME` (da aggiungere a `--set-env-vars` se serve) o lo username Telegram.

Per aggiornare il servizio dopo una modifica al codice (per esempio i pulsanti dei brief del supervisore),
da una checkout aggiornata di `main`:

```bash
gcloud run deploy promo-approvals --project $PROJECT --region europe-west1 --source .
```

Senza `--set-env-vars` e `--set-secrets` il deploy tiene variabili e segreti già configurati. **Non** ripetere
il passo 3 com'è: `--set-*` sostituisce tutto e toglierebbe `PROMO_DISPATCH_INVOKER`,
`PROMO_DISPATCH_AUDIENCE` e `PROMO_GITHUB_DISPATCH_TOKEN` (Cloud Scheduler, vedi
[Pianificazione](#pianificazione-cloud-scheduler--github-actions)). L'indirizzo del servizio non cambia,
quindi il webhook non va ricollegato.

**Dove stanno i video.** Prima versione: disco locale (`PROMO_MEDIA_DIR`) e artifact di GitHub Actions
(14 giorni). Il file non deve viaggiare fra macchine: se manca, admin e publisher lo rigenerano da
`render_spec` (deterministico; lo sha256 viene confrontato e un'eventuale differenza annotata nel log).
Se servisse condividerli davvero, un bucket Cloud Storage privato andrà documentato in `docs/deploy.md`
del gioco.

## Brief del supervisore

Il supervisore (gtp_orchestrator, M4) prepara ogni settimana dei brief; ognuno diventa una bozza con
`python -m promo brief-import <file.json>` (`--day` per il giorno di uscita, `--dry-run` per provare):

```json
{
  "campaign_id": "2026w40-it-tiktok",
  "language": "it",
  "format": "who_is",
  "channel": "tiktok",
  "cta": "Gioca sul bot",
  "angle": "i giramondo della Serie A",
  "facts": ["ha cambiato 7 squadre in 12 anni"],
  "day": "2026-10-02"
}
```

- `language` fra `it`, `en`, `es`; `format` fra `who_is`, `percent`, `ladder`, `journeyman` (la
  `solution` nasce solo dall'indovinello del giorno prima); `channel` fra `tiktok`, `telegram_channel`,
  `x`. Un valore fuori elenco è rifiutato con l'elenco di quelli ammessi, e niente viene scritto.
- `campaign_id`: da 1 a 24 caratteri fra lettere minuscole, cifre e `-` — la stessa regola del gioco
  (`CAMPAIGN_ID`, #218), che altrimenti scarta la campagna in silenzio. Niente maiuscole né `_`. `day` è facoltativo (default: oggi; `--day` ha la precedenza).
- La bozza usa formato e lingua del brief; le schede le sceglie il picker con le stesse regole
  anti-spoiler e la stessa esclusione degli ultimi 30 giorni. Se il formato non ha materiale si ripiega
  su `who_is`, come nella rotazione.
- **I testi restano quelli dei template.** `cta`, `angle` e `facts` finiscono nel campo `brief` del post
  (la dashboard li mostra a chi approva) ma non nella didascalia: se servono, li scrive una persona.
- Il post ha id `<id consueto>-<campaign_id>` e `brief_id` = `campaign_id`. Stesso `campaign_id` e stesso
  giorno → nessun doppione. Le bozze da brief non tolgono la bozza della rotazione di quel giorno.
- Link tracciato: con `PROMO_CAMPAIGN_LINKS=true` è `https://t.me/<bot>?start=src_<canale>-<campaign_id>`;
  senza (default) resta `src_<canale>`. Il gioco legge la campagna da
  michelecoppi/guess_the_player_from_the_path#218: il flag va acceso quando quel bot è in produzione.
- La macchina a stati non cambia: la bozza nasce `draft` e l'approvazione resta umana.

### Brief dal Firestore del supervisore (✅ Usa / ❌ Scarta)

Senza file da scaricare: dall'issue #7 Promo legge i brief direttamente dal Firestore del supervisore
(gtp_orchestrator, [issue #9](https://github.com/michelecoppi/gtp_orchestrator/issues/9)), **in sola
lettura**, e li propone all'admin sul bot approvazioni. Si accende con `PROMO_SUPERVISOR_FIRESTORE_PROJECT`
(il progetto del supervisore, `gtp-orchestrator`); vuota, la funzione è spenta e `brief-import` resta la
via manuale.

Il supervisore scrive `promo_briefs/{campaign_id}` nel **suo** progetto. Promo considera solo i documenti con
`status: "proposed"`, `schema_version` 1 ed `expires_at` futuro; il campo `brief` è esattamente il JSON di
`brief-import` e passa per la stessa validazione. Esempio completo, lo stesso file nei due repository:
`tests/fixtures/supervisor_promo_brief.json`.

Il giro, tutto idempotente:

1. **08:37, `brief-apply`** (dopo `drafts`): i brief che l'admin ha scelto di usare e non ancora importati
   diventano bozze con `briefs.import_brief`, le stesse regole di `brief-import`: picker anti-spoiler,
   testi dei template, `brief_id` = `campaign_id`, stato `draft`. Poi `ask-approval` le manda all'admin
   come tutte le altre.
2. **08:37, `brief-ask`** (dopo `ask-approval`): ogni brief nuovo arriva all'admin come messaggio di testo
   semplice con campagna, settimana, canale, lingua, formato, CTA, angolo e fatti, e i pulsanti
   **✅ Usa** / **❌ Scarta**. Un brief già proposto (o deciso) non si ripropone.
3. **Il pulsante** arriva allo stesso webhook dei post (`promo-approvals`), con i callback
   `brief:use:<campaign_id>` e `brief:skip:<campaign_id>`: `approvals.handle_press` li riconosce dal
   prefisso `brief:` e li passa a `supervisor_briefs.handle_press`. Contano solo i pulsanti dell'admin,
   nella sua chat; la decisione si prende una volta sola (un secondo tocco risponde "Già deciso") e il
   messaggio perde i pulsanti. Un brief usato diventa bozze al giro delle 08:37 successivo; uno scartato
   non torna.

La decisione si salva **in Promo**, nel Firestore del gioco, in `promo_brief_decisions/{campaign_id}`:

| Campo | Contenuto |
| --- | --- |
| `id`, `campaign_id` | la campagna |
| `status` | `asking` (invio in corso), `asked` (in attesa dell'admin), `send_failed` (si ritenta al giro dopo), `used`, `discarded` |
| `brief`, `week`, `supervisor_expires_at` | il brief validato e i dati del supervisore al momento della proposta |
| `asked_at`, `message_id` | quando e con quale messaggio Telegram è stato proposto |
| `decided_by`, `decided_at` | chi ha premuto (`PROMO_ADMIN_NAME` o username Telegram) e quando |
| `imported_at`, `imported_for`, `import_error` | quando è diventato bozze e per quale giorno, o perché no |

Un documento rimasto `asking` (crash durante l'invio) non viene rimandato alla cieca: se il messaggio è
arrivato, i suoi pulsanti funzionano comunque. Il supervisore legge questa collezione in sola lettura
(collector Promo) e mostra l'esito dei brief nel suo brief quotidiano; non scrive mai qui, e Promo non
scrive mai nel Firestore del supervisore. Il webhook non ha bisogno del Firestore del supervisore: il
brief da usare è già copiato nella decisione.

Prove senza effetti: `python -m promo brief-ask --dry-run` elenca i brief che verrebbero proposti (legge i
due Firestore, non scrive e non manda messaggi); `python -m promo brief-apply --dry-run` genera i file delle
bozze senza toccare la coda.

**Messa in funzione (Michele), in quest'ordine:**

1. merge della PR del supervisore (gtp_orchestrator#9): la review *Growth* comincia a scrivere `promo_briefs`;
2. al service account di Promo (quello del secret `GCP_SERVICE_ACCOUNT`) `roles/datastore.viewer` sul
   progetto del supervisore:
   ```bash
   gcloud projects add-iam-policy-binding gtp-orchestrator \
     --member="serviceAccount:<SERVICE_ACCOUNT_DI_PROMO>" --role="roles/datastore.viewer" --condition=None
   ```
3. merge di questa funzione in Promo;
4. nuovo deploy di `promo-approvals` (solo `--source .`, vedi la fine di "Approvazione immediata"), perché
   il webhook conosca i pulsanti `brief:`;
5. solo dopo il deploy, la variabile del repository `PROMO_SUPERVISOR_FIRESTORE_PROJECT=gtp-orchestrator`.
   Con il vecchio webhook un tocco su ✅ Usa / ❌ Scarta verrebbe preso per il pulsante di un post
   sparito: il messaggio perderebbe i pulsanti e il brief resterebbe `asked`.

## Pubblicazione

`python -m promo publish` prende i post `approved` (e i `failed` con meno di 3 tentativi) con
`scheduled_for` passato. Per ciascuno: controllo idempotenza (`external_id` già presente → saltato),
**claim atomico** (transazione Firestore: stato invariato, nessun lucchetto attivo da meno di 30 minuti),
chiamata al canale, poi `published` con `external_id`/`external_url` oppure `failed` con il motivo. Un
errore non ferma mai gli altri post. `--dry-run` fa tutto (compreso rigenerare il video se manca) tranne
la chiamata esterna e non cambia la coda.

Limite noto: se il canale accetta il video ma l'aggiornamento su Firestore fallisce subito dopo, il post
resta con il lucchetto; dopo 30 minuti potrebbe essere ripubblicato. Il caso richiede un guasto di
Firestore esattamente in quel momento.

### Telegram (canale di proprietà)

`sendVideo` con il bot del gioco (`BOT_TOKEN`), **solo** su `PROMO_TELEGRAM_CHANNEL_ID`: la destinazione
non viene mai dal post. Prima del primo invio `getChat` verifica che la chat sia di tipo `channel`; un
gruppo viene rifiutato (`failed`). Testo: didascalia + hashtag + link `src_telegram_channel`.

### TikTok (bozza)

Content Posting API, **upload nella casella dell'utente** (scope `video.upload`): init
(`/v2/post/publish/inbox/video/init/`, `FILE_UPLOAD`), `PUT` a pezzi con `Content-Range`, poi
`/v2/post/publish/status/fetch/` fino a `SEND_TO_USER_INBOX`. La persona apre TikTok, aggiunge audio e
didascalia (copiata dall'admin) e pubblica. `external_id` = `publish_id`; niente URL finché non è
pubblicato.

Il token si rinnova a ogni esecuzione (`/v2/oauth/token/`, `grant_type=refresh_token`). Se TikTok ruota
il refresh token, quello nuovo si salva in **Secret Manager** (`TIKTOK_REFRESH_TOKEN_SECRET`, una nuova
versione) o, in locale, in un file con permessi 600 (`PROMO_TIKTOK_TOKEN_FILE`); senza nessuno dei due un
avviso ricorda di aggiornarlo a mano.

> ⚠️ **Da verificare prima di attivarlo.** L'implementazione segue l'API v2 come documentata finora, ma
> developers.tiktok.com non era raggiungibile quando è stata scritta. Controllare: scope approvati per
> l'app (`video.upload`), regole dei pezzi (oggi: pezzo unico fino a 64 MB, obbligatorio sotto i 5 MB),
> limiti di frequenza, e se le app non ancora revisionate hanno restrizioni (per il Direct Post i post
> di app non revisionate sono solo privati; per l'upload come bozza verificare). Prima prova: `publish
> --id <post> ` su un solo post, poi controllare la casella dell'account.

### X / Threads

Dietro `PROMO_X_ENABLED`, **non ancora implementato**: un post `x` finisce `failed` con "caricare il video
a mano". Instagram e YouTube Shorts sono fuori ambito: il file MP4 si carica a mano.

## Report settimanale

`python -m promo report [--end YYYY-MM-DD] [--notify]`: settimana di 7 giorni che termina `--end`
(escluso, default oggi) confrontata con la precedente. Sola lettura da PostHog con le query HogQL di
`promo/report.py` (solo `SELECT`), tramite la Personal API Key già usata dalla dashboard del gioco.

Contenuto: nuovi utenti per `acquisition_channel` (`bot_started`, `is_new_user=true`); attivazione
(`bot_started` → `daily_completed` entro 7 giorni); ritenzione D7 sulla coorte della settimana precedente
(ancora un `daily_guess_submitted` 7+ giorni dopo l'arrivo); giocatori attivi al giorno; leghe create,
round di gruppo, notifiche attivate; contenuti pubblicati per canale (da `promo_posts`).

**Proposte**, per ogni canale di campagna: *continuare* se un nuovo giocatore costa < 0,50 € e D7 ≥ 20%;
*ridurre o fermare* altrimenti; *dati insufficienti* sotto i 10 utenti. I costi li scrive la persona in
`costs.json` (versionato; chiave = primo giorno della settimana del report):

```json
{ "2026-09-17": { "tiktok": 12.5, "creator": 40 } }
```

`--notify` invia il file Markdown alla sola chat dell'admin (`PROMO_ADMIN_CHAT_ID`).

## Pianificazione (Cloud Scheduler + GitHub Actions)

I lavori girano in `.github/workflows/promo.yml`; chi li avvia all'ora giusta è **Cloud Scheduler**:

| Ora (Europe/Rome) | Job di Cloud Scheduler | Lavoro |
| --- | --- | --- |
| ogni giorno 08:37 | `promo-drafts` | `drafts`: 1 video per lingua nel formato del giorno + la soluzione di ieri, in coda come `draft` |
| ogni giorno 12:23 | `promo-publish` | `publish`: gli `approved` in scadenza |
| venerdì 09:17 | `promo-report` | `report` (+ `--notify` se `PROMO_ADMIN_CHAT_ID` è impostata) |

Con il bot di approvazione, `drafts` è seguito da `brief-apply`, `ask-approval` e `brief-ask` (brief del
supervisore, solo con `PROMO_SUPERVISOR_FIRESTORE_PROJECT`), e `publish` è preceduto da `sync-approvals`
(che con il [webhook](#approvazione-immediata-webhook-su-cloud-run) attivo non fa nulla).

**Perché Cloud Scheduler.** I cron di GitHub Actions sono "best effort" e su questo repository non
partono proprio. Cloud Scheduler parte all'ora esatta e conosce l'ora di Roma, ma non sa avviare un
workflow con un token tenuto in Secret Manager: per questo chiama `/dispatch?command=...` sul servizio
`promo-approvals` (promo/dispatch.py) con un token OIDC di Google. Il servizio accetta solo il service
account `promo-scheduler` (`PROMO_DISPATCH_INVOKER`) e il proprio indirizzo (`PROMO_DISPATCH_AUDIENCE`),
e avvia il workflow con `workflow_dispatch`, mai in `--dry-run`, usando `PROMO_GITHUB_DISPATCH_TOKEN`
(un token GitHub fine-grained, solo questo repository, solo "Actions: Read and write").

In `promo.yml` restano i cron di bozze e pubblicazione come riserva: sono idempotenti, quindi se un
giorno ripartono non fanno danni. Il report no (manderebbe due messaggi).

Una volta sola, dopo il deploy di `promo-approvals` (vedi sopra):

```bash
PROJECT=guess-the-player-from-path-bot
URL=$(gcloud run services describe promo-approvals --project $PROJECT --region europe-west1 --format 'value(status.url)')
SCHED=promo-scheduler@$PROJECT.iam.gserviceaccount.com

gcloud iam service-accounts create promo-scheduler --project $PROJECT --display-name "Promo Studio: Cloud Scheduler"
echo <token GitHub>| gcloud secrets create promo-github-dispatch-token --project $PROJECT --data-file=-
gcloud secrets add-iam-policy-binding promo-github-dispatch-token --project $PROJECT \
  --member serviceAccount:promo-approvals@$PROJECT.iam.gserviceaccount.com --role roles/secretmanager.secretAccessor
gcloud run deploy promo-approvals --project $PROJECT --region europe-west1 --source . \
  --update-env-vars PROMO_DISPATCH_INVOKER=$SCHED,PROMO_DISPATCH_AUDIENCE=$URL \
  --update-secrets PROMO_GITHUB_DISPATCH_TOKEN=promo-github-dispatch-token:latest

job() {  # nome, cron (ora di Roma), comando
  gcloud scheduler jobs create http "$1" --project $PROJECT --location europe-west1 --schedule "$2" \
    --time-zone Europe/Rome --uri "$URL/dispatch?command=$3" --http-method POST --message-body "" \
    --oidc-service-account-email $SCHED --oidc-token-audience $URL --max-retry-attempts 3 --min-backoff 60s
}
job promo-drafts "37 8 * * *" drafts
job promo-publish "23 12 * * *" publish
job promo-report "17 9 * * 5" report
```

Per provare un job subito: `gcloud scheduler jobs run promo-publish --project $PROJECT --location europe-west1`
(dopo la pubblicazione del giorno non pubblica niente). Per sospenderli: `gcloud scheduler jobs pause <job>`.

Rotazione dei formati: lun `who_is`, mar `percent`, mer `journeyman`, gio `who_is`, ven `ladder`,
sab `percent`, dom `who_is` (ripiego su `who_is` se manca materiale). I cron di riserva sono in UTC e
raddoppiati per l'ora legale; `--at-rome-hour` li fa girare solo nelle ore giuste (le bozze fino alle 11,
la pubblicazione fino alle 15). I lavori avviati da Cloud Scheduler o a mano non hanno questo filtro.

Il workflow non gira sui fork (`if: github.repository == 'michelecoppi/promo_studio'`), si autentica a
Google Cloud con Workload Identity Federation (nessuna chiave in un secret) e passa a ogni passo solo i
segreti che servono. Da configurare nel repository:

| Tipo | Nome |
| --- | --- |
| secret | `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` (Firestore + Secret Manager) |
| secret | `BOT_TOKEN`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `POSTHOG_PERSONAL_API_KEY`, `PROMO_APPROVAL_BOT_TOKEN` (facoltativo) |
| variabile | `PROMO_ENABLED`, `PROMO_TELEGRAM_CHANNEL_ID`, `PROMO_ADMIN_CHAT_ID`, `PROMO_ADMIN_NAME`, `POSTHOG_PROJECT_ID`, `TIKTOK_REFRESH_TOKEN_SECRET`, `PROMO_LANGUAGES`, `PROMO_TELEGRAM_LANGUAGES`, `PROMO_SUPERVISOR_FIRESTORE_PROJECT` (facoltativa) |

Il service account ha bisogno di: lettura/scrittura Firestore (`roles/datastore.user`), e per TikTok
`secretmanager.versions.access` + `secretmanager.versions.add` sul solo segreto del refresh token. Per i
brief del supervisore anche `roles/datastore.viewer` sul progetto `gtp-orchestrator` (sola lettura).

`workflow_dispatch` permette di lanciare a mano `drafts`, `publish`, `report` o `sync` (di default in `--dry-run`).

**Se un lavoro fallisce.** Ogni job (`promo.yml` e, solo su `main`, `ci.yml`) termina con il passo "Avviso di
errore su Telegram". Manda all'admin `❌ Promo · lavoro <drafts|publish|report> fallito` con il link alla run,
tramite il bot approvazioni (`PROMO_APPROVAL_BOT_TOKEN`, `PROMO_ADMIN_CHAT_ID`). Usa `curl`, quindi funziona
anche se si rompe l'installazione; senza il bot non fa nulla. Le action sono fissate allo SHA completo
(`uses: owner/action@<sha> # vN`); `.github/dependabot.yml` propone ogni settimana gli aggiornamenti di action
e dipendenze pip.
Cron locale equivalente: `37 8 * * *  python -m promo drafts`, `23 12 * * *  python -m promo publish`,
`17 9 * * 5  python -m promo report --notify`.

## Configurazione

Tutte le variabili sono in `.env.example` (commentate, senza valori). Le principali:

| Variabile | Uso |
| --- | --- |
| `PROMO_ENABLED` | interruttore generale, default off: senza, `drafts` e `publish` non fanno niente |
| `PROMO_LANGUAGES` | default `it,en,es` |
| `PROMO_TELEGRAM_LANGUAGES` | lingue pubblicate anche sul canale Telegram (default `it`; `none` lo spegne) |
| `PROMO_TELEGRAM_CHANNEL_ID` | canale di proprietà |
| `PROMO_APPROVAL_BOT_TOKEN`, `PROMO_ADMIN_CHAT_ID` | approvazione da Telegram (bot dedicato, segreto) |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN` | Content Posting API (segreti) |
| `TIKTOK_REFRESH_TOKEN_SECRET` / `PROMO_TIKTOK_TOKEN_FILE` | dove salvare il refresh token ruotato |
| `POSTHOG_PERSONAL_API_KEY`, `POSTHOG_PROJECT_ID` | già esistenti nel gioco, per il report |
| `GAME_REPO_PATH` | checkout del gioco |
| `PROMO_STORE` | `firestore` (default) o `local` |
| `PROMO_X_ENABLED` | X/Threads (non ancora implementato) |
| `PROMO_CAMPAIGN_LINKS` | link `src_<canale>-<campaign_id>` per le bozze da brief (default off: serve il supporto nel gioco) |
| `PROMO_SUPERVISOR_FIRESTORE_PROJECT` | progetto del supervisore (`gtp-orchestrator`) da cui leggere i brief `promo_briefs`; vuota = funzione spenta |

Segreti: mai nel repository, mai nei log. `Settings.__repr__` li oscura, `promo/log.py` toglie i valori
noti, i token nelle URL della Bot API e i `Bearer`, e passa anche da `services/observability.py`.

## Come si spegne

- Subito: variabile `PROMO_ENABLED=false` (o assente) → `drafts` e `publish` escono senza fare niente;
  il report continua a funzionare (è sola lettura).
- Solo la pubblicazione: rifiutare i post in coda dall'admin, o togliere i segreti del canale (i post
  finiscono `failed` con il motivo, niente esce).
- Del tutto: disabilitare il workflow **Promo** dalla scheda Actions.

## Modifiche richieste nel repository del gioco

Il Promo Studio non modifica il gioco. Quattro cose vanno però fatte lì, con una issue sul Project #2:

1. **`CAMPAIGN_SOURCES`** in `services/product_analytics.py` non contiene `telegram_channel`: finché non
   viene aggiunto, chi arriva da `?start=src_telegram_channel` è contato come `other`.
   `python -m promo doctor` lo segnala.
2. **Pagina admin nel `admin_ui.py`** (facoltativo: la pagina funziona anche da sola con
   `streamlit run admin/app.py`). Una `admin_pages/promo.py` che aggiunge il Promo Studio a `sys.path`
   e chiama `admin.promo_page.render_page(store, theme, settings)`.
3. **Documentazione**: `docs/firestore.md` (nuova collection `promo_posts`, accesso solo server/admin),
   `docs/README.md` e `docs/architecture.md` (link a questo documento). `firestore.rules` non va toccato:
   nega già tutto ai client e l'Admin SDK le ignora.
4. **`campaign_id` nel parametro `/start`** (michelecoppi/guess_the_player_from_the_path#218): il bot deve
   leggere `src_<canale>-<campaign_id>` attribuendo il giocatore al canale e alla campagna. Solo dopo
   si accende `PROMO_CAMPAIGN_LINKS`.

## Stato delle milestone

| # | Milestone | Stato |
| --- | --- | --- |
| M1 | selezione + `who_is`/`solution` da CLI, IT/EN/ES | fatto: `render` produce MP4, copertina, testi; test anti-spoiler |
| M2 | coda `promo_posts` + pagina admin | fatto: bozze in coda, approva/rifiuta/modifica con audit |
| M3 | publisher Telegram + `--dry-run` | fatto, testato con client finti; da provare sul canale vero |
| M4 | publisher TikTok come bozza | implementato, testato con client finti; **da verificare** sulla documentazione e l'app TikTok |
| M5 | `percent`, `ladder`, `journeyman` | fatto, in rotazione nelle bozze |
| M6 | report settimanale | fatto; query HogQL da confermare sui dati reali |
| M7 | workflow | fatto; servono segreti, variabili e Workload Identity |

Test: `python -m pytest -q` (nessuna chiamata di rete; i test di rendering generano MP4 veri su percorsi
corti). Lint: `ruff check .`.
