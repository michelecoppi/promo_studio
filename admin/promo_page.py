"""Dashboard del Promo Studio, in Streamlit. Sei schede:

- **Stato**: interruttore, cosa aspetta te, prossimi lavori, controlli di configurazione, attivita' recente;
- **Coda**: anteprima, Approva / Rifiuta / Modifica didascalia, storico di ogni post;
- **Genera**: un video a mano (formato, lingua, giornata o pool), anteprima e "metti in coda";
- **Pubblica**: cosa esce e quando, simulazione (dry-run) e pubblicazione manuale confermata;
- **Pubblicati e report**: numeri dei contenuti usciti, report settimanale, costi per canale;
- **Guida**: come funziona e come si configura, con i passi gia' fatti spuntati.

Si usa da sola (`streamlit run admin/app.py`) o dentro la dashboard del gioco chiamando
`render_page(store, theme, settings, game_source)` da una pagina di `admin_pages/`.

Qui c'e' solo interfaccia: le regole (chi puo' cambiare stato, da dove a dove, cosa si
registra) stanno in promo/queue.py e valgono uguali per la CLI.
"""
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from promo import copy as promo_copy
from promo import plan, queue, status
from promo.config import LANGUAGES
from promo.models import (
    CHANNELS,
    FORMATS,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
    STATUSES,
)

ICONS = {
    STATUS_DRAFT: "📝", STATUS_APPROVED: "✅", STATUS_REJECTED: "🚫",
    STATUS_PUBLISHED: "📣", STATUS_FAILED: "⚠️",
}
LABELS = {
    STATUS_DRAFT: "da approvare", STATUS_APPROVED: "approvati, in uscita", STATUS_REJECTED: "rifiutati",
    STATUS_PUBLISHED: "pubblicati", STATUS_FAILED: "falliti",
}
LEVEL_ICONS = {status.OK: "🟢", status.WARN: "🟡", status.ERROR: "🔴", status.OFF: "⚪"}
FLAGS = {"it": "🇮🇹", "en": "🇬🇧", "es": "🇪🇸"}
CHANNEL_NAMES = {"tiktok": "TikTok (bozza)", "telegram_channel": "Canale Telegram", "x": "X"}
FORMAT_NAMES = {
    "who_is": "Chi è? (who_is)", "percent": "Solo il X% (percent)", "journeyman": "Giramondo (journeyman)",
    "ladder": "Scala 4 livelli (ladder)", "solution": "Soluzione (solution)",
}
DAYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
ACTIONS_URL = "https://github.com/michelecoppi/promo_studio/actions/workflows/promo.yml"


# ---------------------------------------------------------------------------------------
# Pezzi comuni
# ---------------------------------------------------------------------------------------

def _actor(settings) -> str:
    default = settings.admin_name or st.session_state.get("promo_actor", "")
    actor = st.sidebar.text_input("👤 Il tuo nome (per l'audit)", value=default, key="promo_actor",
                                  help="Ogni approvazione, rifiuto o modifica registra chi l'ha fatta.")
    return actor.strip()


def _post_title(post) -> str:
    return (f"{ICONS.get(post['status'], '')} {FLAGS.get(post.get('language'), '')} "
            f"{FORMAT_NAMES.get(post.get('format'), post.get('format'))} → {CHANNEL_NAMES.get(post.get('channel'), post.get('channel'))}")


def _preview(post, theme, settings, key_prefix=""):
    path = post.get("media_path")
    if path and os.path.exists(path):
        st.video(path)
        return
    cover = post.get("cover_path")
    if cover and os.path.exists(cover):
        st.image(cover, width=240)
    else:
        st.caption("Video non presente su questa macchina.")
    if theme is not None and st.button("🎬 Genera anteprima", key=f"{key_prefix}gen-{post['id']}",
                                       help="Rigenera il video dai dati salvati: identico a quello approvato."):
        with st.spinner("Rendering del video…"):
            path = plan.ensure_media(post, theme, settings.media_dir)
        st.video(path)


def _act(action, store, post_id, actor, **kwargs):
    try:
        action(store, post_id, actor, **kwargs)
        st.toast("Fatto")
        st.rerun()
    except (queue.TransitionError, ValueError) as e:
        st.error(str(e))


def _history_feed(posts, limit=15):
    entries = []
    for post in posts:
        for entry in post.get("history") or []:
            entries.append((entry.get("at") or "", post["id"], entry))
    entries.sort(reverse=True)
    rows = [{
        "quando": status.to_rome(at),
        "post": pid,
        "cambio": f"{entry.get('from') or 'nuovo'} → {entry.get('to')}",
        "chi": entry.get("by"),
        "nota": entry.get("note", ""),
    } for at, pid, entry in entries[:limit]]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.caption("Ancora nessuna attività.")


# ---------------------------------------------------------------------------------------
# Stato
# ---------------------------------------------------------------------------------------

def tab_status(settings, store, game_source, game_error, posts):
    overview = status.queue_overview(posts)
    counts = overview["counts"]

    if settings.enabled:
        st.success("🟢 **Promo Studio attivo**: bozze alle 07:00, pubblicazione alle 12:00, report il venerdì alle 09:00.")
    else:
        st.warning("⚪ **Promo Studio spento** (`PROMO_ENABLED` non è `true`): niente bozze automatiche e niente "
                   "pubblicazione. Puoi comunque generare video dalla scheda **Genera** e guardare tutto il resto.")

    published_week = [p for p in overview["published"]
                      if (p.get("published_at") or "") >= (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")]
    c = st.columns(5)
    c[0].metric("📝 Da approvare", counts[STATUS_DRAFT])
    c[1].metric("✅ In uscita", counts[STATUS_APPROVED])
    c[2].metric("⚠️ Falliti", counts[STATUS_FAILED])
    c[3].metric("📣 Pubblicati (7 gg)", len(published_week))
    c[4].metric("🚫 Rifiutati", counts[STATUS_REJECTED])

    st.subheader("Cosa aspetta te")
    todo = False
    if overview["overdue_drafts"]:
        todo = True
        n = len(overview["overdue_drafts"])
        st.error(f"**{n} {'bozza ha' if n == 1 else 'bozze hanno'} già superato l'orario di uscita** senza "
                 "approvazione: non usciranno finché non le approvi (o rifiuti) nella scheda **Coda**.")
    if overview["waiting"] and not overview["overdue_drafts"]:
        todo = True
        first = overview["waiting"][0]
        st.info(f"**{len(overview['waiting'])} bozze da guardare** nella scheda **Coda**. "
                f"La prima esce il {status.to_rome(first.get('scheduled_for'))}.")
    if overview["failed"]:
        todo = True
        n = len(overview["failed"])
        st.error(f"**{n} {'pubblicazione fallita' if n == 1 else 'pubblicazioni fallite'}.** Il motivo è qui sotto; "
                 "vengono riprovate "
                 f"automaticamente fino a {plan.MAX_ATTEMPTS} volte.")
        st.dataframe([{
            "post": p["id"], "tentativi": p.get("attempts", 0), "errore": p.get("error", ""),
        } for p in overview["failed"][:10]], hide_index=True, width="stretch")
    if not todo:
        st.success("Niente da fare adesso. 👌")

    left, right = st.columns(2)
    with left:
        st.subheader("⏰ Prossimi lavori")
        now = datetime.now(timezone.utc)
        st.dataframe([{
            "lavoro": name,
            "quando (Roma)": f"{DAYS[when.weekday()]} {when.strftime('%d/%m %H:%M')}",
            "": status.humanize(when - now.astimezone(when.tzinfo)),
        } for name, when, _ in status.next_runs(now)], hide_index=True, width="stretch")
        st.caption(f"Girano su GitHub Actions. Ultime esecuzioni: [scheda Actions]({ACTIONS_URL}).")
    with right:
        st.subheader("📤 Prossime uscite approvate")
        if overview["upcoming"]:
            st.dataframe([{
                "uscita": status.to_rome(p.get("scheduled_for")),
                "contenuto": f"{FLAGS.get(p.get('language'), '')} {p.get('format')}",
                "canale": CHANNEL_NAMES.get(p.get("channel"), p.get("channel")),
                "approvato da": p.get("approved_by"),
            } for p in overview["upcoming"][:10]], hide_index=True, width="stretch")
        else:
            st.caption("Nessun post approvato in attesa.")

    st.subheader("🩺 Configurazione")
    results = status.checks(settings, game_source, game_error)
    summary = status.summary(results)
    st.caption(f"🟢 {summary['ok']} ok · 🟡 {summary['warn']} da sistemare · 🔴 {summary['error']} errori · "
               f"⚪ {summary['off']} spenti. Istruzioni complete nella scheda **Guida**.")
    areas = list(dict.fromkeys(c.area for c in results))
    columns = st.columns(2)
    for i, area in enumerate(areas):
        with columns[i % 2].container(border=True):
            st.markdown(f"**{area}**")
            for check in (c for c in results if c.area == area):
                st.markdown(f"{LEVEL_ICONS[check.level]} **{check.name}** — {check.detail}")
                if check.fix and check.level != status.OK:
                    st.caption(f"↳ {check.fix}")

    st.subheader("🕑 Attività recente")
    _history_feed(posts)


# ---------------------------------------------------------------------------------------
# Coda
# ---------------------------------------------------------------------------------------

def _draft_card(store, post, actor, theme, settings):
    left, right = st.columns([1, 2])
    with left:
        _preview(post, theme, settings)
    with right:
        st.markdown(f"#### {_post_title(post)}")
        st.caption(f"Stato **{LABELS.get(post['status'], post['status'])}** · uscita "
                   f"{status.to_rome(post.get('scheduled_for'))} · id `{post['id']}` · "
                   f"sorgenti {', '.join(post.get('source_days') or [])}")
        if post.get("error"):
            st.error(f"Ultimo errore: {post['error']}")
        with st.form(f"copy-{post['id']}"):
            caption = st.text_area("Didascalia", value=post.get("caption", ""), max_chars=queue.MAX_CAPTION)
            hashtags = st.text_input("Hashtag (3-5, separati da spazio)", value=" ".join(post.get("hashtags") or []))
            pinned = st.text_area("Commento fissato (da mettere il giorno dopo)", value=post.get("pinned_comment", ""))
            st.code(post.get("tracking_link", ""), language=None)
            if st.form_submit_button("💾 Salva testi", disabled=not actor or post["status"] not in queue.EDITABLE):
                _act(queue.edit_copy, store, post["id"], actor, caption=caption, hashtags=hashtags.split(),
                     pinned_comment=pinned)
        c1, c2, c3 = st.columns(3)
        if post["status"] == STATUS_DRAFT and c1.button("✅ Approva", key=f"ok-{post['id']}", disabled=not actor,
                                                        type="primary"):
            _act(queue.approve, store, post["id"], actor)
        if post["status"] in (STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED):
            reason = c3.text_input("Motivo (facoltativo)", key=f"why-{post['id']}", label_visibility="collapsed",
                                   placeholder="motivo del rifiuto")
            if c2.button("🚫 Rifiuta", key=f"no-{post['id']}", disabled=not actor):
                _act(queue.reject, store, post["id"], actor, reason=reason)
        if not actor:
            st.caption("✍️ Scrivi il tuo nome nella barra laterale per approvare, rifiutare o modificare.")
        if post.get("channel") == "tiktok" and post["status"] in (STATUS_DRAFT, STATUS_APPROVED):
            st.caption("TikTok: il video arriva come bozza nell'app; lì aggiungi l'audio e incolli la didascalia.")
        with st.expander("📜 Storico"):
            for entry in post.get("history") or []:
                st.text(f"{status.to_rome(entry.get('at'))}  {entry.get('from') or 'nuovo'} → {entry.get('to')}  "
                        f"{entry.get('by')}  {entry.get('note', '')}")


def tab_queue(settings, store, theme, actor, posts):
    days = sorted({p.get("created_for") for p in posts if p.get("created_for")}, reverse=True)
    col1, col2, col3, col4 = st.columns(4)
    day = col1.selectbox("Giorno", ["tutti"] + days)
    lang = col2.selectbox("Lingua", ["tutte"] + list(LANGUAGES), format_func=lambda v: f"{FLAGS.get(v, '')} {v}")
    channel = col3.selectbox("Canale", ["tutti"] + list(CHANNELS))
    statuses = col4.multiselect("Stato", list(STATUSES), default=[STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED],
                                format_func=lambda s: f"{ICONS[s]} {s}")

    def visible(p):
        return ((day == "tutti" or p.get("created_for") == day) and (lang == "tutte" or p.get("language") == lang)
                and (channel == "tutti" or p.get("channel") == channel) and p["status"] in statuses)

    shown = sorted((p for p in posts if visible(p)), key=lambda p: (p.get("created_for") or "", p["id"]), reverse=True)
    drafts = [p for p in shown if p["status"] == STATUS_DRAFT]
    if drafts and actor:
        if st.button(f"✅ Approva tutte le {len(drafts)} bozze visibili", help="Solo quelle filtrate qui sopra."):
            for post in drafts:
                queue.approve(store, post["id"], actor)
            st.rerun()
    st.caption(f"{len(shown)} post con questi filtri.")
    if not shown:
        st.info("Nessun post con questi filtri. Le bozze arrivano ogni mattina alle 07:00, oppure creane una dalla "
                "scheda **Genera**.")
    for post in shown:
        with st.container(border=True):
            _draft_card(store, post, actor, theme, settings)


# ---------------------------------------------------------------------------------------
# Genera
# ---------------------------------------------------------------------------------------

def tab_generate(settings, store, theme, game_source, actor):
    from promo import picker, render

    if game_source is None:
        st.error("Serve il repository del gioco (GAME_REPO_PATH) per scegliere i percorsi.")
        return
    st.caption("Genera un video a mano, guardalo e, se ti piace, mettilo in coda come bozza. "
               "Si usano solo sfide già chiuse o il pool riservato: niente spoiler.")
    c1, c2, c3 = st.columns(3)
    fmt = c1.selectbox("Formato", FORMATS, format_func=lambda f: FORMAT_NAMES[f])
    lang = c2.selectbox("Lingua", LANGUAGES, format_func=lambda v: f"{FLAGS[v]} {v}")
    channel = c3.selectbox("Canale (per il link tracciato)", CHANNELS, format_func=lambda c: CHANNEL_NAMES[c])
    sources = ["Scelta automatica", "Giornata già chiusa", "Pool riservato"]
    if fmt == "ladder":
        sources = sources[:1]
    source = st.radio("Percorso", sources, horizontal=True)
    day = player_id = None
    if source == "Giornata già chiusa":
        today = date.fromisoformat(game_source.today())
        day = st.date_input("Giornata", value=today - timedelta(days=1), max_value=today - timedelta(days=1)).isoformat()
    elif source == "Pool riservato":
        players = sorted(game_source.reserved_players(), key=lambda p: p.get("full_name") or p["id"])
        chosen = st.selectbox("Giocatore del pool", players,
                              format_func=lambda p: f"{p.get('full_name')} · {len(p.get('career') or [])} tappe · "
                                                    f"popolarità {p.get('popularity')}")
        player_id = chosen["id"] if chosen else None
    seed = st.text_input("Seme della scelta", value=game_source.today(),
                         help="Stesso seme e stessi dati → stessa scelta. Cambialo per un altro percorso.")

    if st.button("🎬 Genera video", type="primary"):
        try:
            with st.spinner("Scelgo il percorso e genero il video (15-30 secondi)…"):
                if fmt == "ladder":
                    cards = picker.pick_ladder(game_source, lang, seed=seed)
                elif day:
                    cards = [picker.card_for_day(game_source, day, lang)]
                elif player_id:
                    cards = [picker.card_for_pool_player(game_source, player_id, lang)]
                else:
                    cards = [picker.pick(game_source, fmt, lang, seed=seed)]
                for card in cards:
                    picker.check_format(card, fmt)
                result = render.render(fmt, cards, lang, theme, Path(settings.media_dir) / "manual")
            st.session_state["promo_generated"] = {
                "fmt": fmt, "lang": lang, "channel": channel, "seed": seed, "cards": cards, "result": result,
            }
        except (picker.NoMaterial, picker.SpoilerError, ValueError) as e:
            st.error(str(e))

    generated = st.session_state.get("promo_generated")
    if not generated:
        return
    result, cards = generated["result"], generated["cards"]
    texts = promo_copy.build(generated["fmt"], generated["lang"], generated["channel"], cards, seed=generated["seed"])
    st.divider()
    left, right = st.columns([1, 2])
    with left:
        st.video(result.video_path)
    with right:
        st.markdown(f"**{', '.join(c.player_name for c in cards)}** · {result.duration:.1f} s · "
                    f"generato in {result.extra.get('seconds', '?')} s")
        for card in cards:
            origin = f"giornata del {card.source_day}" if card.source_day else "pool riservato"
            pct = f" · indovinato dal {card.percent_solved}%" if card.percent_solved is not None else ""
            st.caption(f"{card.player_name}: {len(card.stops)} tappe · {origin}{pct}")
        st.text_area("Didascalia + hashtag (da copiare)", texts.caption_with_hashtags(), height=90)
        st.text_area("Commento fissato (il giorno dopo)", texts.pinned_comment, height=70)
        st.code(texts.tracking_link, language=None)
        with open(result.video_path, "rb") as fh:
            st.download_button("⬇️ Scarica MP4", fh, file_name=Path(result.video_path).name, mime="video/mp4")
        publish_day = st.date_input("Giorno di uscita", value=date.today(), min_value=date.today(), key="gen_day")
        if st.button("➕ Metti in coda come bozza", disabled=store is None):
            post = plan.make_post(fmt=generated["fmt"], lang=generated["lang"], channel=generated["channel"],
                                  cards=cards, day=publish_day.isoformat(), media=result, seed=generated["seed"])
            if store.create(post):
                st.success(f"Bozza `{post['id']}` in coda: approvala dalla scheda **Coda**.")
            else:
                st.warning(f"`{post['id']}` esiste già in coda.")


# ---------------------------------------------------------------------------------------
# Pubblica
# ---------------------------------------------------------------------------------------

def tab_publish(settings, store, theme, actor, posts):
    from promo.publishers import build_publishers

    overview = status.queue_overview(posts)
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    due = [p for p in overview["upcoming"] if (p.get("scheduled_for") or "") <= now_s]
    later = [p for p in overview["upcoming"] if (p.get("scheduled_for") or "") > now_s]
    retry = [p for p in overview["failed"] if int(p.get("attempts") or 0) < plan.MAX_ATTEMPTS]

    st.caption("Di norma pubblica il workflow alle 12:00. Da qui puoi simulare o pubblicare subito: escono solo "
               "i post **approvati** con l'orario già passato, una volta sola.")
    c = st.columns(3)
    c[0].metric("Pronti ora", len(due))
    c[1].metric("Da riprovare", len(retry))
    c[2].metric("Più tardi", len(later))

    for title, items in (("Escono alla prossima pubblicazione (pronti + da riprovare)", due + retry),
                         ("Più tardi", later)):
        if items:
            st.markdown(f"**{title}**")
            st.dataframe([{
                "uscita": status.to_rome(p.get("scheduled_for")), "post": p["id"],
                "canale": CHANNEL_NAMES.get(p.get("channel"), p.get("channel")), "stato": p["status"],
                "tentativi": p.get("attempts", 0),
            } for p in items], hide_index=True, width="stretch")

    col1, col2 = st.columns(2)
    if col1.button("🧪 Simula (dry-run)", help="Fa tutto tranne la chiamata a Telegram/TikTok. Non cambia la coda."):
        with st.spinner("Simulazione…"):
            lines = plan.publish_due(store, build_publishers(settings), theme, settings, dry_run=True)
        st.code("\n".join(lines), language=None)
    confirm = col2.checkbox("Confermo: pubblica davvero adesso")
    disabled = not (confirm and actor and settings.enabled)
    if col2.button("📣 Pubblica ora", type="primary", disabled=disabled):
        with st.spinner("Pubblicazione…"):
            lines = plan.publish_due(store, build_publishers(settings), theme, settings)
        st.code("\n".join(lines), language=None)
    if not settings.enabled:
        col2.caption("Disabilitato: PROMO_ENABLED non è attivo.")
    elif not actor:
        col2.caption("Scrivi il tuo nome nella barra laterale.")


# ---------------------------------------------------------------------------------------
# Pubblicati e report
# ---------------------------------------------------------------------------------------

def _week_start(today: date) -> str:
    return (today - timedelta(days=7)).isoformat()


def _costs_editor(settings):
    st.subheader("💶 Costi per canale")
    st.caption("Li scrivi tu (lo strumento non legge conti pubblicitari né spende). Chiave = primo giorno della "
               "settimana del report.")
    path = Path(settings.costs_file)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    week = st.date_input("Settimana che inizia il", value=date.fromisoformat(_week_start(date.today())),
                         key="costs_week").isoformat()
    current = data.get(week) or {}
    rows = [{"canale": ch, "euro": float(current.get(ch, 0.0))}
            for ch in sorted(set(current) | {"tiktok", "creator", "reddit", "instagram"})]
    edited = st.data_editor(rows, num_rows="dynamic", hide_index=True, key=f"costs-{week}",
                            column_config={"euro": st.column_config.NumberColumn("euro", min_value=0.0, step=0.5,
                                                                                 format="%.2f €")})
    if st.button("💾 Salva costi"):
        data[week] = {r["canale"]: float(r["euro"] or 0) for r in edited if r.get("canale") and float(r["euro"] or 0) > 0}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success(f"Salvato in {path}")


def tab_results(settings, store, game_source, posts):
    from promo import report

    published = status.queue_overview(posts)["published"]
    st.subheader("📣 Contenuti pubblicati")
    if published:
        c = st.columns(3)
        c[0].metric("Totale", len(published))
        by_channel = Counter(p.get("channel") for p in published)
        c[1].metric("TikTok", by_channel.get("tiktok", 0))
        c[2].metric("Canale Telegram", by_channel.get("telegram_channel", 0))
        per_day = Counter((p.get("published_at") or "")[:10] for p in published)
        start = date.today() - timedelta(days=29)
        st.bar_chart({"pubblicati": {(start + timedelta(days=i)).isoformat(): per_day.get(
            (start + timedelta(days=i)).isoformat(), 0) for i in range(30)}})
        st.dataframe([{
            "pubblicato": status.to_rome(p.get("published_at")),
            "contenuto": f"{FLAGS.get(p.get('language'), '')} {p.get('format')}",
            "canale": CHANNEL_NAMES.get(p.get("channel"), p.get("channel")),
            "approvato da": p.get("approved_by"),
            "link": p.get("external_url") or "",
            "id esterno": p.get("external_id") or "",
        } for p in published], hide_index=True, width="stretch",
            column_config={"link": st.column_config.LinkColumn("link")})
    else:
        st.caption("Ancora nulla di pubblicato.")

    st.subheader("📊 Report settimanale")
    reports_dir = Path(settings.reports_dir)
    if st.button("Genera il report adesso", disabled=game_source is None,
                 help="Sola lettura da PostHog: nuovi giocatori, attivazione e ritenzione per canale."):
        with st.spinner("Interrogo PostHog…"):
            result = report.weekly(game_source, store, settings)
            path = report.write(result, reports_dir)
        st.success(f"Salvato in {path}")
    files = sorted(reports_dir.glob("promo-report-*.md"), reverse=True) if reports_dir.exists() else []
    if files:
        chosen = st.selectbox("Report", files, format_func=lambda p: p.stem.replace("promo-report-", "settimana dal "))
        st.markdown(chosen.read_text(encoding="utf-8"))
    else:
        st.caption("Nessun report salvato su questa macchina. Quelli del venerdì arrivano su Telegram "
                   "(se PROMO_ADMIN_CHAT_ID è impostata) e negli artifact di GitHub Actions.")

    _costs_editor(settings)


# ---------------------------------------------------------------------------------------
# Guida
# ---------------------------------------------------------------------------------------

GUIDE = """
### Come funziona

1. **07:00, bozze.** Per ogni lingua il sistema sceglie un percorso (solo sfide già chiuse o il pool
   riservato: niente spoiler), genera il video e i testi e li mette in **Coda** come *da approvare*. Se ieri
   c'era un indovinello, prepara anche il video con la soluzione.
2. **Tu approvi.** Nella scheda **Coda** guardi il video, correggi la didascalia se serve, **Approva** o
   **Rifiuta**. Senza approvazione non esce niente.
3. **12:00, pubblicazione.** Gli approvati escono: sul canale Telegram direttamente, su TikTok come
   **bozza** nell'app (aggiungi audio e didascalia e pubblichi tu).
4. **Venerdì 09:00, report.** Nuovi giocatori per canale, quanti tornano dopo 7 giorni, e una proposta
   (continuare / ridurre / fermare). I costi li inserisci tu nella scheda **Pubblicati e report**.

Formati a rotazione: lun *Chi è?*, mar *Solo il X%*, mer *Giramondo*, gio *Chi è?*, ven *Scala*,
sab *Solo il X%*, dom *Chi è?*.

### Per spegnere tutto
`PROMO_ENABLED=false` (nel `.env` e nelle variabili del repository GitHub), oppure disabilita il workflow
**Promo** dalla scheda Actions. Il report continua a funzionare perché è solo lettura.
"""

SETUP_STEPS = (
    ("Repository del gioco", "Gioco", "Repository del gioco",
     "Clona `guess_the_player_from_the_path` accanto a questo repository e imposta `GAME_REPO_PATH` nel `.env`, "
     "poi `pip install -r $GAME_REPO_PATH/requirements.txt`. Servono anche le credenziali Firestore del bot "
     "(`FIREBASE_CREDENTIALS_PATH`)."),
    ("Video", "Video", "ffmpeg", "`pip install -r requirements.txt` installa ffmpeg (imageio-ffmpeg)."),
    ("Canale Telegram", "Canali", "Telegram (canale del gioco)",
     "Crea un canale Telegram, aggiungi il bot del gioco come **amministratore**, poi `BOT_TOKEN` e "
     "`PROMO_TELEGRAM_CHANNEL_ID=@nomecanale`. Prova con **Pubblica → Simula**."),
    ("TikTok", "Canali", "TikTok (bozze)",
     "Crea un'app su developers.tiktok.com con lo scope `video.upload` (verifica lì limiti e restrizioni per "
     "le app non revisionate), fai l'autorizzazione OAuth una volta per ottenere il refresh token, poi "
     "`TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`. In automatico: salva il refresh token "
     "in Secret Manager e indica `TIKTOK_REFRESH_TOKEN_SECRET`."),
    ("Link tracciati", "Gioco", "Link tracciati",
     "Nel repository del gioco aggiungi `\"telegram_channel\"` a `CAMPAIGN_SOURCES` in "
     "`services/product_analytics.py`, altrimenti chi arriva dal canale è contato come *other*."),
    ("Report", "Report", "PostHog (sola lettura)",
     "`POSTHOG_PERSONAL_API_KEY` e `POSTHOG_PROJECT_ID` (le stesse della dashboard del gioco). Facoltativo: "
     "`PROMO_ADMIN_CHAT_ID` per ricevere il report su Telegram."),
    ("Accensione", "Generale", "Interruttore PROMO_ENABLED", "`PROMO_ENABLED=true` quando sei pronto."),
)

ACTIONS_SETUP = """
### Automatico su GitHub Actions

1. **Google Cloud** (stesso progetto del bot): service account con ruolo *Cloud Datastore User* (+ per TikTok
   accesso e aggiunta versioni al segreto del refresh token), collegato a GitHub con **Workload Identity
   Federation** (come il workflow di backup del gioco).
2. **GitHub → Settings → Secrets and variables → Actions**
   - *Secrets*: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `BOT_TOKEN`, `TIKTOK_CLIENT_KEY`,
     `TIKTOK_CLIENT_SECRET`, `POSTHOG_PERSONAL_API_KEY`
   - *Variables*: `PROMO_ENABLED=true`, `PROMO_TELEGRAM_CHANNEL_ID`, `PROMO_ADMIN_CHAT_ID`, `POSTHOG_PROJECT_ID`,
     `TIKTOK_REFRESH_TOKEN_SECRET`
3. **Prova**: *Actions → Promo → Run workflow* (dry-run di default).

Documentazione completa: `docs/promo-studio.md`.
"""


def tab_guide(settings, game_source, game_error):
    st.markdown(GUIDE)
    st.markdown("### Configurazione, passo per passo")
    results = {(c.area, c.name): c for c in status.checks(settings, game_source, game_error)}
    for title, area, name, text in SETUP_STEPS:
        check = results.get((area, name))
        icon = LEVEL_ICONS[check.level] if check else "⚪"
        with st.expander(f"{icon} {title}" + (f" — {check.detail}" if check else ""),
                         expanded=bool(check and check.level in (status.WARN, status.ERROR))):
            st.markdown(text)
    st.markdown(ACTIONS_SETUP)
    st.markdown("### Comandi utili")
    st.code("\n".join((
        "python -m promo doctor                 # stessi controlli della scheda Stato",
        "python -m promo render --format who_is --lang it --day 2026-09-10",
        "python -m promo drafts                 # bozze del giorno",
        "python -m promo approve <id> --by Nome",
        "python -m promo publish --dry-run",
        "python -m promo report",
        "python -m promo brief --lang it        # testo per un creator pagato, con #adv",
    )), language="bash")


# ---------------------------------------------------------------------------------------

def render_page(store, theme, settings, game_source=None, game_error=None, store_error=None):
    st.title("📣 Promo Studio")
    st.caption("La macchina prepara, la persona approva: niente esce senza un ✅.")
    actor = _actor(settings)
    try:
        posts = store.list() if store is not None else []
    except Exception as e:  # coda irraggiungibile: le altre schede restano utili
        posts, store_error = [], store_error or str(e)
    if store_error:
        st.error(f"Coda promo_posts non raggiungibile: {store_error}")

    counts = Counter(p["status"] for p in posts)
    st.sidebar.markdown("### Stato")
    st.sidebar.markdown(("🟢 attivo" if settings.enabled else "⚪ spento") + " · " +
                        " · ".join(f"{ICONS[s]} {counts.get(s, 0)}" for s in (STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED)))
    if st.sidebar.button("🔄 Aggiorna"):
        st.rerun()

    drafts = counts.get(STATUS_DRAFT, 0)
    tabs = st.tabs([
        "🏠 Stato", f"📝 Coda ({drafts})" if drafts else "📝 Coda", "🎬 Genera", "📤 Pubblica",
        "📊 Pubblicati e report", "📖 Guida",
    ])
    with tabs[0]:
        tab_status(settings, store, game_source, game_error, posts)
    with tabs[1]:
        tab_queue(settings, store, theme, actor, posts)
    with tabs[2]:
        tab_generate(settings, store, theme, game_source, actor)
    with tabs[3]:
        if store is None:
            st.error("Coda non disponibile.")
        else:
            tab_publish(settings, store, theme, actor, posts)
    with tabs[4]:
        tab_results(settings, store, game_source, posts)
    with tabs[5]:
        tab_guide(settings, game_source, game_error)
