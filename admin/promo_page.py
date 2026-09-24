"""Pagina "Promo" per l'approvazione (§5.4), in Streamlit.

Si usa in due modi:
- da sola: `streamlit run admin/app.py` (dal repository promo_studio);
- dentro la dashboard del gioco: `from admin.promo_page import render_page` in una pagina di
  `admin_pages/` (vedi docs/promo-studio.md), passando la coda e il tema.

Qui c'e' solo interfaccia: le regole (chi puo' cambiare stato, da dove a dove, cosa si
registra) stanno in promo/queue.py e valgono uguali per la CLI.
"""
import os
from collections import Counter

import streamlit as st

from promo import plan, queue
from promo.models import (
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
FLAGS = {"it": "🇮🇹", "en": "🇬🇧", "es": "🇪🇸"}


def _actor(settings) -> str:
    default = settings.admin_name or st.session_state.get("promo_actor", "")
    actor = st.sidebar.text_input("Il tuo nome (registrato nell'audit)", value=default, key="promo_actor")
    return actor.strip()


def _preview(post, theme, settings):
    path = post.get("media_path")
    if path and os.path.exists(path):
        st.video(path)
        return
    cover = post.get("cover_path")
    if cover and os.path.exists(cover):
        st.image(cover, width=260)
    if st.button("Genera anteprima", key=f"gen-{post['id']}"):
        with st.spinner("Rendering del video (deterministico, identico a quello approvato)…"):
            path = plan.ensure_media(post, theme, settings.media_dir)
        st.video(path)


def _draft_card(store, post, actor, theme, settings):
    left, right = st.columns([1, 2])
    with left:
        _preview(post, theme, settings)
    with right:
        st.markdown(
            f"**{ICONS.get(post['status'], '')} {post['status']}** · {FLAGS.get(post['language'], '')} "
            f"`{post['format']}` → **{post['channel']}** · uscita {post.get('scheduled_for', '')}"
        )
        st.caption(f"id `{post['id']}` · sorgenti {', '.join(post.get('source_days') or [])}")
        if post.get("error"):
            st.error(post["error"])
        with st.form(f"copy-{post['id']}"):
            caption = st.text_area("Didascalia", value=post.get("caption", ""), max_chars=queue.MAX_CAPTION)
            hashtags = st.text_input("Hashtag (3-5, separati da spazio)", value=" ".join(post.get("hashtags") or []))
            pinned = st.text_area("Commento fissato (il giorno dopo)", value=post.get("pinned_comment", ""))
            st.text_input("Link tracciato", value=post.get("tracking_link", ""), disabled=True)
            if st.form_submit_button("💾 Modifica didascalia", disabled=not actor):
                try:
                    queue.edit_copy(store, post["id"], actor, caption=caption, hashtags=hashtags.split(),
                                    pinned_comment=pinned)
                    st.success("Testi salvati")
                    st.rerun()
                except (ValueError, queue.TransitionError) as e:
                    st.error(str(e))
        c1, c2, _ = st.columns([1, 1, 3])
        if post["status"] == STATUS_DRAFT and c1.button("✅ Approva", key=f"ok-{post['id']}", disabled=not actor):
            _act(queue.approve, store, post["id"], actor)
        if post["status"] in (STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED):
            if c2.button("🚫 Rifiuta", key=f"no-{post['id']}", disabled=not actor):
                _act(queue.reject, store, post["id"], actor)
        if not actor:
            st.caption("Scrivi il tuo nome nella barra laterale per approvare o rifiutare.")
        with st.expander("Storico"):
            for entry in post.get("history") or []:
                st.text(f"{entry.get('at')}  {entry.get('from')} → {entry.get('to')}  {entry.get('by')}  {entry.get('note', '')}")


def _act(action, store, post_id, actor):
    try:
        action(store, post_id, actor)
        st.rerun()
    except queue.TransitionError as e:
        st.error(str(e))


def render_page(store, theme, settings):
    st.header("📣 Promo")
    st.caption("La macchina prepara, la persona approva: niente esce senza un ✅ qui o con `python -m promo approve`.")
    actor = _actor(settings)
    posts = store.list()
    counts = Counter(p["status"] for p in posts)
    st.write(" · ".join(f"{ICONS[s]} {s}: **{counts.get(s, 0)}**" for s in STATUSES))

    days = sorted({p.get("created_for") for p in posts if p.get("created_for")}, reverse=True)
    col1, col2, col3 = st.columns(3)
    day = col1.selectbox("Giorno", ["tutti"] + days)
    lang = col2.selectbox("Lingua", ["tutte"] + list(settings.languages))
    statuses = col3.multiselect("Stato", list(STATUSES), default=[STATUS_DRAFT, STATUS_APPROVED, STATUS_FAILED])

    def visible(p):
        return ((day == "tutti" or p.get("created_for") == day) and (lang == "tutte" or p.get("language") == lang)
                and p["status"] in statuses)

    queue_posts = sorted((p for p in posts if visible(p)), key=lambda p: (p.get("created_for") or "", p["id"]),
                         reverse=True)
    if not queue_posts:
        st.info("Nessun post con questi filtri.")
    for post in queue_posts:
        with st.container(border=True):
            _draft_card(store, post, actor, theme, settings)

    st.subheader("Pubblicati")
    published = sorted((p for p in posts if p["status"] == STATUS_PUBLISHED),
                       key=lambda p: p.get("published_at") or "", reverse=True)
    st.dataframe(
        [{
            "pubblicato": p.get("published_at"), "id": p["id"], "canale": p["channel"],
            "approvato da": p.get("approved_by"), "link": p.get("external_url") or p.get("external_id"),
        } for p in published],
        column_config={"link": st.column_config.LinkColumn("link")},
        width="stretch",
    )
