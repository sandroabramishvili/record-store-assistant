"""My collection (GET/POST/DELETE /collection). The assistant never recommends what's in here."""

import pandas as pd
import streamlit as st

import api_client as api
from components import call

st.title('My collection')
st.caption("Records you already own. The assistant won't recommend these, and uses them to learn your taste.")

data = call(api.get_collection)
if data:
    stats = data['stats']
    a, b = st.columns(2)
    a.metric('Records', stats['total'])
    b.metric('Linked to our catalog', stats['in_catalog'], help='Genre and decade are known only for these.')
    if stats['by_genre']:
        st.caption('By genre (catalog records)')
        st.dataframe(pd.DataFrame(sorted(stats['by_genre'].items(), key=lambda kv: -kv[1]), columns=['Genre', 'Records']),
                     hide_index=True)

    for item in data['items']:
        left, right = st.columns([6, 1])
        left.write(f'**{item["artist"]}** — {item["title"]}' + ('' if item['record_id'] else '  ·  _not in our catalog_'))
        if right.button('Remove', key=f'rm-{item["id"]}'):
            if call(api.remove_from_collection, item['id'], success='Removed'):
                st.rerun()

st.subheader('Add a record you own')
from_catalog, free_text = st.tabs(['From our catalog', 'Anything else'])
with from_catalog:
    query = st.text_input('Find it in the catalog', placeholder='Artist or title')
    if query.strip():
        found = call(api.list_records, search=query.strip(), size=10)
        options = {f'{r["artist"]} — {r["title"]} ({r["release_year"]})': r['id'] for r in (found or {}).get('items', [])}
        if options:
            choice = st.selectbox('Which one?', list(options))
            if st.button('Add to my collection', type='primary'):
                if call(api.add_to_collection, record_id=options[choice], success='Added'):
                    st.rerun()
        else:
            st.caption('Nothing found. Use the "Anything else" tab.')
with free_text, st.form('free-text', clear_on_submit=True):
    artist = st.text_input('Artist')
    title = st.text_input('Title')
    if st.form_submit_button('Add'):
        if call(api.add_to_collection, artist=artist.strip(), title=title.strip(), success='Added'):
            st.rerun()
