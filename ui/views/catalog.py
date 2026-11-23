"""Browse the catalog (GET /records) and reserve a copy directly (POST /reservations:
the same reservation service the chat uses, spec 7.6)."""

import streamlit as st

import api_client as api
from components import CONDITION_HELP, CONDITIONS, call, money, pressing
from style import esc

st.title('Browse catalog')

with st.form('filters'):
    c1, c2, c3 = st.columns(3)
    search = c1.text_input('Search', placeholder='Artist, title, label, catalog no.')
    genre = c2.text_input('Genre', placeholder='e.g. jazz')
    decade = c3.selectbox('Decade', [None, *range(1950, 2030, 10)], format_func=lambda d: 'Any' if d is None else f'{d}s')
    c4, c5, c6 = st.columns(3)
    max_price = c4.number_input('Max price ($)', min_value=0.0, step=5.0, value=0.0, help='0 = no limit')
    min_condition = c5.selectbox('At least', [None, *CONDITIONS], format_func=lambda c: 'Any grade' if c is None else c,
                                 help=CONDITION_HELP)
    in_stock = c6.checkbox('Only records in stock', value=True)
    if st.form_submit_button('Search', type='primary'):
        st.session_state['catalog_page'] = 1                   # a new search starts on page 1

page = st.session_state.setdefault('catalog_page', 1)
result = call(api.list_records, search=search.strip(), genre=genre.strip().lower(), decade=decade,
              max_price=max_price or None, min_condition=min_condition, in_stock=True if in_stock else None,
              page=page, size=20)


@st.dialog('Record details', width='large')
def details(record_id: int) -> None:
    record = call(api.get_record, record_id)
    if not record:
        return
    st.subheader(f'{record["artist"]} — {record["title"]}')
    st.caption(f'{record["label"]} {record["catalog_number"] or ""} · {record["country"] or ""} · '
               f'{record["format"]} · {pressing(record)} ({record["release_year"]}, first released '
               f'{record["original_year"]}) · {", ".join(record["genres"] + record["styles"])}')
    st.write(record['description'])
    if record['tracklist']:
        with st.expander('Tracklist'):
            st.markdown('\n'.join(f'- {t["position"]} {t["title"]}' for t in record['tracklist']))
    copies = [c for c in record['copies'] if c['status'] == 'in_stock']
    st.markdown(f'**Copies in stock: {len(copies)}**')
    for copy in copies:
        left, mid, right = st.columns([3, 1, 1], vertical_alignment='center')
        left.write(f'Media {copy["media_condition"]} · sleeve {copy["sleeve_condition"]}'
                   + (f' · {copy["notes"]}' if copy['notes'] else ''))
        mid.markdown(f'<span class="rs-price">{esc(money(copy["price"]))}</span>', unsafe_allow_html=True)
        if right.button('Reserve', key=f'reserve-{copy["id"]}', type='primary'):
            if call(api.reserve, copy['id']):
                st.success('Reserved for 48 hours. See "My reservations".')
    a, b = st.columns(2)
    if a.button('I own this (add to collection)'):
        call(api.add_to_collection, record_id=record['id'], success='Added to your collection')
    if b.button('Tell me when a copy comes in'):
        call(api.add_to_wantlist, record['artist'], record['title'], None, None, success='Added to your wantlist')


if result:
    st.caption(f'{result["total"]} records')
    for record in result['items']:
        with st.container(key=f'card-r{record["id"]}'):
            left, mid, right = st.columns([4, 2, 1], vertical_alignment='center')
            left.markdown(f'<span class="rs-card-title">{esc(record["artist"])} — {esc(record["title"])}</span><br>'
                          f'<span class="rs-card-meta">{record["original_year"]} · {esc(pressing(record))} · '
                          f'{esc(record["label"])} · {esc(", ".join(record["genres"]))}</span>',
                          unsafe_allow_html=True)
            if record['available_copies']:
                mid.markdown(f'{record["available_copies"]} in stock, from '
                             f'<span class="rs-price">{esc(money(record["lowest_price"]))}</span>',
                             unsafe_allow_html=True)
            else:
                mid.markdown('<span class="rs-stamp">sold out</span>', unsafe_allow_html=True)
            if right.button('Details', key=f'details-{record["id"]}'):
                details(record['id'])
    pages = max(1, -(-result['total'] // result['size']))
    prev, info, nxt = st.columns([1, 2, 1])
    if prev.button('← Previous', disabled=page <= 1):
        st.session_state['catalog_page'] -= 1
        st.rerun()
    info.caption(f'Page {page} of {pages}')
    if nxt.button('Next →', disabled=page >= pages):
        st.session_state['catalog_page'] += 1
        st.rerun()
