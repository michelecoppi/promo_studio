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
| `services/path_image.py` (`_color_for_team`, `_years_label`, palette) | coerenza grafica con il bot |
| `webapp/src/assets/fonts/BarlowCondensed-SemiBold.ttf`, `services/fonts.py` | font dei titoli e di ripiego |
| `services/product_analytics_query.py`, `services/product_analytics.py::CAMPAIGN_SOURCES` | report e link `src_` |
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
streamlit run admin/app.py             # approva / rifiuta / modifica la didascalia
python -m promo list --status draft
python -m promo approve <id> --by michele
python -m promo publish --dry-run      # cosa uscirebbe, senza chiamare nessuno
python -m promo publish
python -m promo report                 # reports/promo-report-<inizio>.md
python -m promo brief --lang it        # testo per un creator pagato, con #adv (da copiare a mano)
```

Senza Firestore si può provare tutto con `PROMO_STORE=local` (coda in `promo_posts.json`), usando
`--pool-player` per i video.

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

Nessuno stemma, logo, foto o musica: solo nomi dei club e colori generati (`_color_for_team`).

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
`render_spec` (le schede usate, per rigenerare il video identico). Le date sono stringhe ISO 8601 UTC.

**Dove stanno i video.** Prima versione: disco locale (`PROMO_MEDIA_DIR`) e artifact di GitHub Actions
(14 giorni). Il file non deve viaggiare fra macchine: se manca, admin e publisher lo rigenerano da
`render_spec` (deterministico; lo sha256 viene confrontato e un'eventuale differenza annotata nel log).
Se servisse condividerli davvero, un bucket Cloud Storage privato andrà documentato in `docs/deploy.md`
del gioco.

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

## Pianificazione (GitHub Actions)

`.github/workflows/promo.yml`:

| Ora (Europe/Rome) | Lavoro |
| --- | --- |
| ogni giorno 07:00 | `drafts`: 1 video per lingua nel formato del giorno + la soluzione di ieri, in coda come `draft` |
| ogni giorno 12:00 | `publish`: gli `approved` in scadenza |
| venerdì 09:00 | `report` (+ `--notify` se `PROMO_ADMIN_CHAT_ID` è impostata) |

Rotazione dei formati: lun `who_is`, mar `percent`, mer `journeyman`, gio `who_is`, ven `ladder`,
sab `percent`, dom `who_is` (ripiego su `who_is` se manca materiale). I cron sono in UTC e raddoppiati per
l'ora legale; `--at-rome-hour` fa girare il lavoro solo nell'ora giusta, e tutti i lavori sono idempotenti.

Il workflow non gira sui fork (`if: github.repository == 'michelecoppi/promo_studio'`), si autentica a
Google Cloud con Workload Identity Federation (nessuna chiave in un secret) e passa a ogni passo solo i
segreti che servono. Da configurare nel repository:

| Tipo | Nome |
| --- | --- |
| secret | `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` (Firestore + Secret Manager) |
| secret | `BOT_TOKEN`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `POSTHOG_PERSONAL_API_KEY` |
| variabile | `PROMO_ENABLED`, `PROMO_TELEGRAM_CHANNEL_ID`, `PROMO_ADMIN_CHAT_ID`, `POSTHOG_PROJECT_ID`, `TIKTOK_REFRESH_TOKEN_SECRET`, `PROMO_LANGUAGES`, `PROMO_TELEGRAM_LANGUAGES` |

Il service account ha bisogno di: lettura/scrittura Firestore (`roles/datastore.user`), e per TikTok
`secretmanager.versions.access` + `secretmanager.versions.add` sul solo segreto del refresh token.

`workflow_dispatch` permette di lanciare a mano `drafts`, `publish` o `report` (di default in `--dry-run`).
Cron locale equivalente: `0 7 * * *  python -m promo drafts`, `0 12 * * *  python -m promo publish`,
`0 9 * * 5  python -m promo report --notify`.

## Configurazione

Tutte le variabili sono in `.env.example` (commentate, senza valori). Le principali:

| Variabile | Uso |
| --- | --- |
| `PROMO_ENABLED` | interruttore generale, default off: senza, `drafts` e `publish` non fanno niente |
| `PROMO_LANGUAGES` | default `it,en,es` |
| `PROMO_TELEGRAM_LANGUAGES` | lingue pubblicate anche sul canale Telegram (default `it`) |
| `PROMO_TELEGRAM_CHANNEL_ID` | canale di proprietà |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN` | Content Posting API (segreti) |
| `TIKTOK_REFRESH_TOKEN_SECRET` / `PROMO_TIKTOK_TOKEN_FILE` | dove salvare il refresh token ruotato |
| `POSTHOG_PERSONAL_API_KEY`, `POSTHOG_PROJECT_ID` | già esistenti nel gioco, per il report |
| `GAME_REPO_PATH` | checkout del gioco |
| `PROMO_STORE` | `firestore` (default) o `local` |
| `PROMO_X_ENABLED` | X/Threads (non ancora implementato) |

Segreti: mai nel repository, mai nei log. `Settings.__repr__` li oscura, `promo/log.py` toglie i valori
noti, i token nelle URL della Bot API e i `Bearer`, e passa anche da `services/observability.py`.

## Come si spegne

- Subito: variabile `PROMO_ENABLED=false` (o assente) → `drafts` e `publish` escono senza fare niente;
  il report continua a funzionare (è sola lettura).
- Solo la pubblicazione: rifiutare i post in coda dall'admin, o togliere i segreti del canale (i post
  finiscono `failed` con il motivo, niente esce).
- Del tutto: disabilitare il workflow **Promo** dalla scheda Actions.

## Modifiche richieste nel repository del gioco

Il Promo Studio non modifica il gioco. Tre cose vanno però fatte lì, con una issue sul Project #2:

1. **`CAMPAIGN_SOURCES`** in `services/product_analytics.py` non contiene `telegram_channel`: finché non
   viene aggiunto, chi arriva da `?start=src_telegram_channel` è contato come `other`.
   `python -m promo doctor` lo segnala.
2. **Pagina admin nel `admin_ui.py`** (facoltativo: la pagina funziona anche da sola con
   `streamlit run admin/app.py`). Una `admin_pages/promo.py` che aggiunge il Promo Studio a `sys.path`
   e chiama `admin.promo_page.render_page(store, theme, settings)`.
3. **Documentazione**: `docs/firestore.md` (nuova collection `promo_posts`, accesso solo server/admin),
   `docs/README.md` e `docs/architecture.md` (link a questo documento). `firestore.rules` non va toccato:
   nega già tutto ai client e l'Admin SDK le ignora.

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
