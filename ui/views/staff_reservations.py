"""Staff: every customer's reservations (GET /reservations as staff), complete or cancel."""

from datetime import datetime

import streamlit as st

import api_client as api
from components import call, money

st.title('📋 All reservations')
status = st.segmented_control('Status', ['active', 'completed', 'cancelled', 'expired'], default='active')
data = call(api.list_reservations, status=status)
if data:
    st.caption(f'{data["total"]} reservations')
    for r in data['items']:
        with st.container(border=True):
            left, mid, right = st.columns([4, 2, 2])
            left.markdown(f'**{r["artist"]} — {r["title"]}** · copy #{r["inventory_item_id"]}')
            left.caption(f'customer #{r["user_id"]} · {r["media_condition"]}/{r["sleeve_condition"]} · {money(r["price"])}')
            if r['status'] == 'active':
                mid.write(f'until {datetime.fromisoformat(r["expires_at"]):%d %b %H:%M} UTC')
                done, cancel = right.columns(2)
                if done.button('Paid & collected', key=f'done-{r["id"]}', type='primary'):
                    if call(api.complete_reservation, r['id'], success='Completed: the copy is sold'):
                        st.rerun()
                if cancel.button('Cancel', key=f'cancel-{r["id"]}'):
                    if call(api.cancel_reservation, r['id'], success='Cancelled: the copy is back on sale'):
                        st.rerun()
            else:
                mid.caption(r['status'])
