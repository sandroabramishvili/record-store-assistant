"""My reservations (GET /reservations, DELETE /reservations/{id}). Holds last 48 hours."""

from datetime import datetime

import streamlit as st

import api_client as api
from components import call, money
from style import esc

st.title('My reservations')
st.caption('A reservation holds a copy for 48 hours. Pay and collect it in the shop. You can have up to 3 at a time.')

status = st.segmented_control('Show', ['active', 'completed', 'cancelled', 'expired'], default='active')
data = call(api.list_reservations, status=status)
if data:
    if not data['items']:
        st.caption('Nothing here.')
    for r in data['items']:
        with st.container(key=f'card-res{r["id"]}'):
            left, mid, right = st.columns([4, 2, 1], vertical_alignment='center')
            left.markdown(f'<span class="rs-card-title">{esc(r["artist"])} — {esc(r["title"])}</span><br>'
                          f'<span class="rs-card-meta">media {esc(r["media_condition"])} / sleeve '
                          f'{esc(r["sleeve_condition"])}</span> &nbsp;<span class="rs-price">{esc(money(r["price"]))}</span>',
                          unsafe_allow_html=True)
            if r['status'] == 'active':
                until = datetime.fromisoformat(r['expires_at'])
                mid.write(f'Held until **{until:%a %d %b, %H:%M}** UTC')
                if right.button('Cancel', key=f'cancel-{r["id"]}'):
                    if call(api.cancel_reservation, r['id'], success='Cancelled; the copy is back on sale'):
                        st.rerun()
            else:
                mid.markdown(f'<span class="rs-stamp">{esc(r["status"])}</span>', unsafe_allow_html=True)
