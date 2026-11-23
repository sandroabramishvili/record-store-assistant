"""Wantlist with matches (GET/POST/DELETE /wantlist). A match = a copy in stock that fits."""

import streamlit as st

import api_client as api
from components import CONDITION_HELP, CONDITIONS, call, money, record_card

st.title('Wantlist')
st.caption("Records you're looking for. When a matching copy comes in, it shows up here.")

data = call(api.get_wantlist)
if data:
    if data['match_count']:
        st.success(f'{data["match_count"]} of your wanted records are in stock now!', icon='🎉')
    for entry in data['items']:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            what = f'**{entry["artist"]}** — {entry["title"]}' if entry['title'] else f'Anything by **{entry["artist"]}**'
            limits = [f'up to {money(entry["max_price"])}' if entry['max_price'] else '',
                      f'at least {entry["min_condition"]}' if entry['min_condition'] else '']
            left.markdown(what + ('  ·  ' + ', '.join(x for x in limits if x) if any(limits) else ''))
            if right.button('Remove', key=f'rm-{entry["id"]}'):
                if call(api.remove_from_wantlist, entry['id'], success='Removed'):
                    st.rerun()
            if match := entry.get('match'):
                st.markdown('**In stock now:**')
                record_card({**match, 'year': None, 'pressing': None, 'status': 'in_stock'}, key=f'w{entry["id"]}')
                if st.button('Reserve this copy', key=f'reserve-{match["item_id"]}', type='primary'):
                    if call(api.reserve, match['item_id'], success='Reserved for 48 hours'):
                        st.rerun()

st.subheader('Add to your wantlist')
with st.form('add', clear_on_submit=True):
    a, b = st.columns(2)
    artist = a.text_input('Artist')
    title = b.text_input('Title (empty = anything by this artist)')
    c, d = st.columns(2)
    max_price = c.number_input('Max price ($)', min_value=0.0, step=5.0, help='0 = no limit')
    min_condition = d.selectbox('At least', [None, *CONDITIONS], help=CONDITION_HELP,
                                format_func=lambda x: 'Any grade' if x is None else x)
    if st.form_submit_button('Add', type='primary'):
        if call(api.add_to_wantlist, artist.strip(), title.strip(), str(max_price) if max_price else None,
                min_condition, success='Added to your wantlist'):
            st.rerun()
