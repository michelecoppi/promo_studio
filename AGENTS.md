# Regole per gli agenti

Punto di ingresso breve per agenti (AI o persone). Il protocollo di lavoro è quello del gioco e **non
si copia qui**: lo si segue come scritto in

- [`AGENTS.md`](https://github.com/michelecoppi/guess_the_player_from_the_path/blob/main/AGENTS.md) del gioco;
- [`docs/agent-protocol.md`](https://github.com/michelecoppi/guess_the_player_from_the_path/blob/main/docs/agent-protocol.md)
  (stato non fidato, sequenza di lavoro, agenti in parallelo, documentazione);
- [`docs/github-workflow.md`](https://github.com/michelecoppi/guess_the_player_from_the_path/blob/main/docs/github-workflow.md)
  (branch, commit, PR, merge).

Documento primario di Promo: [`docs/promo-studio.md`](docs/promo-studio.md). Il codice è la verità, i
documenti sono la mappa.

## Protocollo (in breve)

1. **Fonte di verità dei task:** le issue di `michelecoppi/promo_studio`. Oggi non sono nel
   [Project #2](https://github.com/users/michelecoppi/projects/2) del gioco: niente campi Status/WIP da
   leggere o spostare. Non inventare task e resta nello scopo dell'issue assegnata.
2. **Stato fresco:** `git fetch origin`, lavora da `origin/main` aggiornato, controlla diff e CI dello
   SHA di testa esatto prima di modificare o revisionare.
3. **Branch** `<tipo>/<issue>-<slug>` (`feat`, `fix`, `refactor`, `chore`, …), un'issue per branch e
   per PR. **Mai commit su `main`**, mai force-push, mai toccare il checkout di un altro.
4. **Commit** in [Conventional Commits](https://www.conventionalcommits.org/), in italiano.
5. **PR** con `Closes #<n>` solo se tutti i criteri dell'issue sono soddisfatti, altrimenti `Refs #<n>`
   con quello che manca.
6. **Prima della PR** gli stessi comandi della CI (`.github/workflows/ci.yml`):
   ```bash
   pip install -r requirements-dev.txt -r requirements-admin.txt
   ruff check .
   python -m pytest -q
   ```
   I test usano il gioco finto (`tests/fakes.py`): niente rete, niente Firestore.
7. **CI verde sullo SHA di testa** e **nessun merge senza l'approvazione esplicita di Michele**.
8. Aggiorna `docs/promo-studio.md` nella stessa PR se cambi comportamento, configurazione, pianificazione
   o contratti.

## Vincoli propri di Promo

**Il principio: la macchina prepara, la persona approva.**

- **Approvazione solo umana.** Un agente non esegue mai `python -m promo approve`/`reject` (né
  `edit-caption`) sulla coda vera, non preme **Approva**/**Rifiuta**/**Pubblica ora** nella dashboard
  (`admin/`) e non preme i pulsanti del bot approvazioni. La macchina a stati (`promo/queue.py`)
  rifiuta gli attori di sistema: non aggirarla.
- **Mai pubblicare in prova su canali reali** (canale Telegram del gioco, TikTok, X). Per provare:
  - `python -m promo publish --dry-run` (tutto tranne la chiamata esterna, la coda non cambia);
  - `drafts --dry-run` e `brief-import --dry-run` (generano i file senza scrivere la coda);
  - `render` (solo file in `out/`);
  - demo offline di [`docs/demo-setup.md`](docs/demo-setup.md), Livello 1: `PROMO_OFFLINE=true` con
    `PROMO_STORE=local` (coda nel file `promo_posts.json`, niente Firestore).

  Non lanciare verso servizi veri comandi che scrivono a persone o chat: `ask-approval`,
  `report --notify`, `tiktok-auth`.
- **`workflow_dispatch` di `promo.yml` sempre con `dry_run` attivo** (è il default), salvo richiesta
  esplicita di Michele. Non cambiare il default né i cron senza un'issue.
- **Webhook del bot approvazioni intoccabile.** Nessun agente chiama `setWebhook`, `deleteWebhook` o
  `getUpdates` sul bot (`PROMO_APPROVAL_BOT_TOKEN`), né `python -m promo approval-webhook --set/--delete`,
  né `sync-approvals` a mano: staccherebbero o consumerebbero le approvazioni che arrivano al servizio
  Cloud Run `promo-approvals` (`promo/approval_service.py`).
- **Infrastruttura solo con Michele:** nessuna modifica a Cloud Scheduler (`promo-drafts`,
  `promo-publish`, `promo-report`), Cloud Run, Secret Manager, IAM, Workload Identity, secrets e
  variabili del repository, né `PROMO_ENABLED`. Si propone nell'issue o nella PR.
- **Segreti:** non leggere né stampare `.env`, `firebase-key.json` o altre chiavi; non metterli in log,
  commit o messaggi. La documentazione delle variabili è [`.env.example`](.env.example) (solo nomi,
  nessun valore).
- **Dipendenza dal gioco:** Promo importa il codice del gioco solo da `promo/game.py`, tramite
  `GAME_REPO_PATH`. Un cambio a quel contratto (funzioni di `services.*`, dataset, `promo_posts`,
  `CAMPAIGN_SOURCES`, link `src_`) si coordina con un'issue nel gioco
  (`michelecoppi/guess_the_player_from_the_path`, Project #2). Non modificare il repository del gioco da
  qui.
- **Consumatori di `promo_posts`:** il supervisore `michelecoppi/gtp_orchestrator` legge la collezione in
  sola lettura. Cambiare stati o campi (`status`, `created_for`, `scheduled_for`, `published_at`,
  `history`, …) va segnalato con un'issue su `gtp_orchestrator`, citata nella PR.
  Il contratto è [`docs/schemas/promo_post.v1.json`](docs/schemas/promo_post.v1.json), verificato da
  `tests/test_post_schema.py`: si aggiorna nella stessa PR del cambio. Lo stesso vale per le decisioni
  sui brief del supervisore (`promo_brief_decisions`, lette dal suo collector):
  [`docs/schemas/promo_brief_decision.v1.json`](docs/schemas/promo_brief_decision.v1.json), verificato da
  `tests/test_brief_decision_schema.py`.
