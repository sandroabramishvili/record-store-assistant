"""My reservations (GET /reservations, DELETE /reservations/{id}). Holds last 48 hours."""

from datetime import datetime

import streamlit as st

import api_client as api
from components import call, money

st.title('🧾 My reservations')
st.caption('A reservation holds a copy for 48 hours. Pay and collect it in the shop. You can have up to 3 at a time.')

status = st.segmented_control('Show', ['active', 'completed', 'cancelled', 'expired'], default='active')
data = call(api.list_reservations, status=status)
if data:
    if not data['items']:
        st.caption('Nothing here.')
    for r in data['items']:
        with st.container(border=True):
            left, mid, right = st.columns([4, 2, 1])
            left.markdown(f'**{r["artist"]} — {r["title"]}**')
            left.caption(f'{r["media_condition"]}/{r["sleeve_condition"]} · {money(r["price"])}')
            if r['status'] == 'active':
                until = datetime.fromisoformat(r['expires_at'])
                mid.write(f'Held until **{until:%a %d %b, %H:%M}** UTC')
                if right.button('Cancel', key=f'cancel-{r["id"]}'):
                    if call(api.cancel_reservation, r['id'], success='Cancelled; the copy is back on sale'):
                        st.rerun()
            else:
                mid.caption(r['status'])
