# Promo Studio — configurazione per una demo (istruzioni per un agente)

Questo documento è scritto per essere eseguito da un agente (o da una persona) passo per passo.
Ogni passo ha il comando, il risultato atteso e cosa fare se non torna. I passi marcati **👤 PERSONA**
richiedono qualcosa che solo il maintainer può fare (account, segreti): l'agente si ferma lì, chiede,
e riprende.

Repository:
- Promo Studio: `https://github.com/michelecoppi/promo_studio` (branch `main`)
- Gioco: `https://github.com/michelecoppi/guess_the_player_from_the_path` (pubblico, solo lettura)

Documentazione completa: `docs/promo-studio.md`.

---

## Regole per l'agente (leggere prima)

1. **Non pubblicare niente di reale.** In questa demo `publish` si usa solo con `--dry-run`, tranne nel
   Livello 3 e solo se la persona ha dato un canale Telegram **di prova**.
2. **Nessun segreto nel repository.** Token e chiavi vanno solo nel file `.env` (già in `.gitignore`).
   Non stamparli, non incollarli nei messaggi, non fare commit di `.env`, `firebase-key.json`,
   `promo_posts.json`, `promo-media/`, `out/`, `reports/`.
3. **Non modificare il codice** per far funzionare la demo. Se un passo fallisce, riporta l'errore esatto
   (vedi "Problemi comuni") invece di cambiare file in `promo/`.
4. **Non toccare il repository del gioco** (solo clone in lettura).
5. **Nessun messaggio a persone o gruppi**, nessuna spesa.
6. Alla fine compila il **Resoconto** (ultima sezione) e restituiscilo alla persona.

---

## Livelli della demo

| Livello | Cosa mostra | Serve dalla persona |
| --- | --- | --- |
| **1. Offline** | video in 5 formati e 3 lingue, bozze in coda, approvazione, dashboard, pubblicazione simulata, report | niente |
| **2. Dati veri** | stesse cose con le sfide passate vere (percentuali reali, formato `percent`) | file di chiave Firestore del bot |
| **3. Canale Telegram di prova** | un video pubblicato davvero su un canale di prova | un canale Telegram di prova + token del bot |
| **4. Automatico (facoltativo)** | GitHub Actions, TikTok, report da PostHog | account Google Cloud, app TikTok, chiavi PostHog |

Il Livello 1 è sufficiente per una demo completa dell'interfaccia. Fai i livelli in ordine e fermati
dove la persona ti dice.

---

## Requisiti

- Python **3.11 o superiore** (`python --version` oppure `python3 --version`).
- Git.
- ~1 GB di spazio libero. ffmpeg **non** va installato: arriva con `imageio-ffmpeg`.
- Sistema operativo: Linux, macOS o Windows. Sotto, dove i comandi differiscono, ci sono entrambe le
  versioni (bash / PowerShell).

---

## Livello 1 — Demo offline (nessuna credenziale)

### 1.1 Clonare i due repository affiancati

```bash
mkdir promo-demo && cd promo-demo
git clone https://github.com/michelecoppi/promo_studio
git clone --depth 1 https://github.com/michelecoppi/guess_the_player_from_the_path
cd promo_studio
```

**Atteso:** due cartelle sorelle, `promo_studio/` e `guess_the_player_from_the_path/`.
Da qui in poi tutti i comandi si lanciano **dentro `promo_studio/`**.

### 1.2 Ambiente Python e dipendenze

bash (Linux/macOS):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-admin.txt
```

PowerShell (Windows):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -r requirements-admin.txt
```

**Atteso:** installazione senza errori (Pillow, imageio-ffmpeg, requests, streamlit, pytest, ruff).
Le dipendenze del gioco (firebase-admin ecc.) **non** servono per il Livello 1.

### 1.3 Verifica che il codice funzioni

```bash
python -m pytest -q
```

**Atteso:** `97 passed` (circa 30 secondi; alcuni test generano video veri).
Se fallisce, fermati e riporta l'output: non andare avanti.

### 1.4 File `.env` per la demo offline

Crea `promo_studio/.env` con **esattamente** questo contenuto:

```dotenv
PROMO_ENABLED=true
PROMO_OFFLINE=true
PROMO_STORE=local
GAME_REPO_PATH=../guess_the_player_from_the_path
PROMO_ADMIN_NAME=Demo
PROMO_LANGUAGES=it,en,es
```

Significato:
- `PROMO_OFFLINE=true`: niente Firestore; si usa solo il **pool riservato** del dataset (giocatori che
  non usciranno mai come sfida del giorno, quindi niente spoiler).
- `PROMO_STORE=local`: la coda dei post è il file `promo_posts.json`.
- `PROMO_ENABLED=true`: abilita bozze e pubblicazione (qui comunque solo simulata).

### 1.5 Controllo della configurazione

```bash
python -m promo doctor
```

**Atteso:** `0 errori`. Righe `[OK]` per PROMO_ENABLED, repository del gioco, font Barlow Condensed,
ffmpeg, archivio (file locale). Sono **normali** in demo questi `[WARN]`:
- `Modalità offline`;
- `Link tracciati … src_telegram_channel` (va sistemato nel gioco, non qui);
- Telegram, TikTok, PostHog non configurati.

Se compare `[ERR ] Gioco: Repository del gioco`, `GAME_REPO_PATH` è sbagliato: controlla che
`../guess_the_player_from_the_path/services/player_pool.py` esista.

### 1.6 Un video a mano per ogni formato

```bash
python -m promo render --format who_is     --lang it --out out
python -m promo render --format journeyman --lang en --out out
python -m promo render --format ladder     --lang es --out out
python -m promo render --format solution   --lang it --pool-player crespo --out out
```

**Atteso** per ciascuno (10-30 secondi l'uno):
```
video:     out/<nome>.mp4 (<durata> s, generato in <n> s)
copertina: out/<nome>.png
testi:     out/<nome>.txt
```
Durate attese: `who_is` 18-25 s, `journeyman` 20-30 s, `ladder` 25-30 s, `solution` 8-12 s.
Nel `.txt` ci sono didascalia, hashtag, commento fissato e link `https://t.me/guess_the_player_from_path_bot?start=src_tiktok`.

**Normale in offline:** `python -m promo render --format percent …` risponde
`errore: nessun percorso adatto al formato percent`: la percentuale di chi ha indovinato esiste solo per le
sfide vere (Livello 2). Non è un bug.

Facoltativo, controllo della zona sicura di TikTok: aggiungi `--check-layout` a un comando `render`
(fallisce se un testo finisce sotto i pulsanti o sotto la didascalia).

### 1.7 Bozze del giorno (come il lavoro delle 07:00)

```bash
python -m promo drafts
python -m promo list
```

**Atteso:** 4 righe `… creata` (IT su TikTok e sul canale Telegram, EN e ES su TikTok) in circa 30-60
secondi, e `list` mostra 4 post in stato `draft`. Il formato dipende dal giorno della settimana
(lun/gio/dom `who_is`, mar/sab `percent`, mer `journeyman`, ven `ladder`); in offline `percent` ripiega
su `who_is`.

Rilanciare `python -m promo drafts` **non** crea doppioni (risponde "bozze … già presenti"): è voluto.

### 1.8 Approvazione dalla riga di comando

Prendi gli id da `python -m promo list` e:

```bash
python -m promo approve <id-1> --by Demo
python -m promo reject  <id-2> --by Demo --reason "prova demo"
python -m promo edit-caption <id-3> --by Demo --caption "Didascalia modificata in demo"
python -m promo list
```

**Atteso:** `<id-1>: approved`, `<id-2>: rejected`, `<id-3>: didascalia aggiornata`.
Senza `--by` (e senza `PROMO_ADMIN_NAME`) l'approvazione viene rifiutata: ogni cambio registra chi lo fa.

### 1.9 Pubblicazione simulata

```bash
python -m promo publish --dry-run
```

**Atteso:** una riga `(dry-run) …` per ogni post **approvato** (es. "upload come bozza TikTok di … (N
byte, 1 pezzi)" oppure "sendVideo su (canale non configurato) …"). La coda non cambia.

⚠️ **Non** lanciare `python -m promo publish` senza `--dry-run` nel Livello 1: senza credenziali il post
finirebbe `failed` ("non configurati"). Non è pericoloso, ma sporca la demo.

### 1.10 Report settimanale

```bash
python -m promo report
```

**Atteso:** `report: reports/promo-report-<data>.md`. Senza PostHog il file dice "⚠️ Dati incompleti"
con il motivo: è corretto. Mostra comunque la struttura del report e le regole di spesa.

Testo per un creator pagato (da copiare a mano, con la dichiarazione `#adv`):
```bash
python -m promo brief --lang it
```

### 1.11 Dashboard

```bash
streamlit run admin/app.py
```

Si apre `http://localhost:8501`. Se l'agente non ha un browser, basta verificare che il terminale
mostri `Local URL: http://localhost:8501` senza traceback e lasciare il comando alla persona.

Cosa mostrare, scheda per scheda:

| Scheda | Da mostrare |
| --- | --- |
| 🏠 **Stato** | banner "attivo", numeri della coda, "Cosa aspetta te", prossimi lavori con conto alla rovescia, configurazione con 🟢/🟡 e come sistemare |
| 📝 **Coda** | anteprima video, modifica didascalia, **Approva** / **Rifiuta**, storico |
| 🎬 **Genera** | formato + lingua + "Pool riservato" → **Genera video** → anteprima → **Metti in coda come bozza** |
| 📤 **Pubblica** | cosa esce e quando, **Simula (dry-run)**. Non premere **Pubblica ora** |
| 📊 **Pubblicati e report** | report generato al passo 1.10, editor dei costi (**Salva costi** scrive `costs.json`) |
| 📖 **Guida** | come funziona e configurazione passo per passo |

Per fermare: `Ctrl+C` nel terminale.

**Fine Livello 1.** Compila il Resoconto e chiedi alla persona se proseguire.

---

## Livello 2 — Dati veri (sola lettura da Firestore)

Mostra le sfide passate vere: percentuali reali, formato `percent`, soluzione del giorno dopo.
La coda resta **locale** (`PROMO_STORE=local`): la demo non scrive su Firestore.

### 2.1 👤 PERSONA: chiave Firestore

Chiedi alla persona il file di chiave del service account del bot (lo stesso usato da `admin_ui.py` del
gioco, di solito `firebase-key.json`). Salvalo **fuori** da `promo_studio/` o dentro con quel nome esatto
(è in `.gitignore`). Non aprirlo e non stamparne il contenuto.

### 2.2 Dipendenze del gioco

```bash
pip install -r ../guess_the_player_from_the_path/requirements.txt
```

### 2.3 `.env`

Modifica `.env`: **togli** la riga `PROMO_OFFLINE=true` e aggiungi:

```dotenv
FIREBASE_CREDENTIALS_PATH=/percorso/assoluto/firebase-key.json
```

Lascia `PROMO_STORE=local`.

### 2.4 Verifica

```bash
python -m promo doctor
python -m promo render --format who_is  --lang it --day <una data passata, es. 7 giorni fa, YYYY-MM-DD> --out out
python -m promo render --format percent --lang it --out out
```

**Atteso:** nessuna riga "Modalità offline"; il video della giornata indicata; `percent` ora funziona e
l'aggancio dice "SOLO IL X%" con la percentuale vera.

La regola anti-spoiler in pratica: `--day <oggi>` o una data futura deve rispondere
`errore: la sfida del … non e' ancora chiusa` — è la prova da mostrare.

Se `render --day` risponde "nessuna sfida utilizzabile", quella giornata non ha dati: prova un'altra data.

Poi ripeti 1.7-1.11 (conviene prima cancellare `promo_posts.json` per partire da una coda vuota).

---

## Livello 3 — Pubblicazione vera su un canale Telegram di prova

### 3.1 👤 PERSONA

1. Creare un **canale** Telegram di prova (non un gruppo), pubblico con un username, es. `@gtp_promo_demo`.
2. Aggiungere il bot del gioco (o un bot di prova creato con @BotFather) come **amministratore** del
   canale, con il permesso di pubblicare.
3. Dare all'agente il **token del bot** e lo username del canale.

### 3.2 `.env`

Aggiungi:
```dotenv
BOT_TOKEN=<token del bot>
PROMO_TELEGRAM_CHANNEL_ID=@gtp_promo_demo
PROMO_TELEGRAM_LANGUAGES=it
```

### 3.3 Pubblicazione di **un** post

```bash
python -m promo doctor                     # Telegram deve essere [OK]
python -m promo drafts                     # se la coda è vuota
python -m promo list --status draft        # scegli un id che finisce con -telegram_channel
python -m promo approve <id> --by Demo
python -m promo publish --dry-run --id <id> --now
python -m promo publish --id <id> --now
```

`--now` serve perché i post sono programmati per le 12:00 del loro giorno: senza, prima di quell'ora
`publish` risponde "niente da pubblicare". Funziona solo insieme a `--id` (un post alla volta) e solo su
post già **approvati**.

**Atteso:** `<id>: pubblicato (https://t.me/gtp_promo_demo/<n>)` e il video compare nel canale. Rilanciare
`publish --id <id>` non lo ripubblica (idempotente).

Se la chat indicata è un gruppo, il post finisce `failed` con "si pubblica solo su un canale di
proprietà": è la protezione voluta.

---

## Livello 4 — Facoltativo: automatico, TikTok, PostHog

Richiede account e configurazioni che solo la persona può fare. L'agente può preparare la checklist,
ma non deve inventare valori. Dettagli completi in `docs/promo-studio.md`, sezioni "Pubblicazione" e
"Pianificazione".

- **PostHog (report con numeri veri):** 👤 `POSTHOG_PERSONAL_API_KEY` e `POSTHOG_PROJECT_ID` (le stesse della
  dashboard del gioco) nel `.env`, poi `python -m promo report`.
- **TikTok (bozze nell'app):** 👤 app su developers.tiktok.com con scope `video.upload`, autorizzazione OAuth
  una volta per ottenere il refresh token; poi `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
  `TIKTOK_REFRESH_TOKEN` nel `.env`. Prima prova su **un** post con `publish --id <id> --now`. Verificare sulla
  documentazione TikTok limiti e restrizioni per le app non revisionate.
- **GitHub Actions (bozze 07:00, pubblicazione 12:00, report venerdì 09:00):** 👤 service account Google Cloud con
  Workload Identity Federation; secrets e variabili elencati in `docs/promo-studio.md`; prova con
  *Actions → Promo → Run workflow* (dry-run di default). Per la demo **non** serve.
- **Nel repository del gioco** (issue separata, non in questa demo): aggiungere `"telegram_channel"` a
  `CAMPAIGN_SOURCES` in `services/product_analytics.py`.

---

## Pulizia dopo la demo

```bash
rm -rf out promo-media reports promo_posts.json    # PowerShell: Remove-Item -Recurse -Force out,promo-media,reports,promo_posts.json
```
Tenere `.env` solo se la persona vuole riusarlo; altrimenti cancellarlo (contiene segreti nei livelli 2-4).
Se nel Livello 3 è stato usato un canale di prova, la persona può cancellare i messaggi da Telegram.

---

## Problemi comuni

| Sintomo | Causa | Cosa fare |
| --- | --- | --- |
| `GAME_REPO_PATH non impostato` / `non sembra il repository del gioco` | percorso sbagliato | controlla che `../guess_the_player_from_the_path/services/player_pool.py` esista; usa un percorso assoluto se serve |
| `No module named 'firebase_admin'` | Livello 2 senza dipendenze del gioco, oppure `PROMO_OFFLINE` tolto per sbaglio | `pip install -r ../guess_the_player_from_the_path/requirements.txt`, oppure rimetti `PROMO_OFFLINE=true` |
| `nessun percorso adatto al formato percent` | offline: non ci sono percentuali | normale; usa il Livello 2 |
| `N tappe non vanno bene per who_is … (prova journeyman)` | giocatore con troppe tappe | usa `--format journeyman` o un altro giocatore |
| `la sfida del … non e' ancora chiusa` | data di oggi o futura | voluto (anti-spoiler): usa una data passata |
| `PROMO_ENABLED non attivo` | manca nel `.env` | aggiungi `PROMO_ENABLED=true` |
| `serve --by <nome>` | approvazione senza nome | aggiungi `--by Demo` o `PROMO_ADMIN_NAME=Demo` |
| dashboard: "Coda promo_posts non raggiungibile" | `PROMO_STORE` non è `local` e Firestore non è configurato | `PROMO_STORE=local` |
| `streamlit: command not found` | dipendenze admin mancanti o venv non attivo | attiva il venv, `pip install -r requirements-admin.txt` |
| PowerShell: `Activate.ps1 cannot be loaded` | policy di esecuzione | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, poi riattiva |

---

## Resoconto da restituire alla persona

```
Livello raggiunto: 1 / 2 / 3 / 4
Sistema: <OS>, Python <versione>
Test: <N passed / errori>
doctor: <n ok, n da sistemare, n errori> — errori: <elenco o "nessuno">
Video generati: <formato, lingua, durata, tempo di generazione> ...
Bozze create: <numero> — approvate: <id> — rifiutate: <id>
publish --dry-run: <righe principali>
Pubblicazione reale (solo Livello 3): <link o "non fatta">
Report: <percorso file>
Dashboard: avviata su <URL> — schede verificate: <elenco>
Problemi incontrati: <sintomo → cosa hai fatto>
Cose che richiedono la persona: <elenco>
```
