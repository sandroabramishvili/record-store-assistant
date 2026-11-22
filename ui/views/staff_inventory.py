"""Staff: catalog and copies (POST /records, POST/PUT/PATCH/DELETE /inventory).
New copies are matched against wantlists and indexed for the assistant by the API."""

import streamlit as st

import api_client as api
from components import CONDITION_HELP, CONDITIONS, call, money, pressing

FORMATS = ['LP', 'EP', '7"', '10"', '12"']

st.title('📦 Inventory')
copies_tab, new_tab = st.tabs(['Copies of a record', 'New record'])

with copies_tab:
    query = st.text_input('Find a record', placeholder='Artist, title, label or catalog no.')
    found = call(api.list_records, search=query.strip(), size=20) if query.strip() else None
    options = {f'{r["artist"]} — {r["title"]} ({r["release_year"]}, {pressing(r)})': r['id']
               for r in (found or {}).get('items', [])}
    if query.strip() and not options:
        st.caption('No record found. Add it in the "New record" tab.')
    if options:
        record = call(api.get_record, options[st.selectbox('Record', list(options))])
        if record:
            st.caption(f'{record["label"]} {record["catalog_number"] or ""} · record #{record["id"]}')
            for copy in record['copies']:
                with st.container(border=True):
                    head, status_col, actions = st.columns([3, 1, 2])
                    head.markdown(f'**#{copy["id"]}** · {copy["media_condition"]}/{copy["sleeve_condition"]} · '
                                  f'**{money(copy["price"])}**' + (f' · {copy["notes"]}' if copy['notes'] else ''))
                    status_col.caption(copy['status'])
                    if copy['status'] == 'in_stock' and actions.button('Mark sold', key=f'sold-{copy["id"]}'):
                        if call(api.set_copy_status, copy['id'], 'sold', success='Marked as sold'):
                            st.rerun()
                    if copy['status'] == 'sold' and actions.button('Back in stock', key=f'back-{copy["id"]}'):
                        if call(api.set_copy_status, copy['id'], 'in_stock', success='Back in stock'):
                            st.rerun()
                    if actions.button('Delete', key=f'del-{copy["id"]}'):
                        if call(api.delete_copy, copy['id'], success='Deleted'):
                            st.rerun()
                    with st.expander('Edit'), st.form(f'edit-{copy["id"]}'):
                        a, b, c = st.columns(3)
                        media = a.selectbox('Media', CONDITIONS, CONDITIONS.index(copy['media_condition']))
                        sleeve = b.selectbox('Sleeve', CONDITIONS, CONDITIONS.index(copy['sleeve_condition']))
                        price = c.number_input('Price ($)', min_value=0.01, value=float(copy['price']), step=1.0)
                        notes = st.text_input('Notes', value=copy['notes'] or '')
                        if st.form_submit_button('Save'):
                            if call(api.update_copy, copy['id'], media, sleeve, f'{price:.2f}', notes.strip(),
                                    success='Saved'):
                                st.rerun()

            st.markdown('**Add a copy**')
            with st.form('add-copy', clear_on_submit=True):
                a, b, c = st.columns(3)
                media = a.selectbox('Media', CONDITIONS, index=2, help=CONDITION_HELP)
                sleeve = b.selectbox('Sleeve', CONDITIONS, index=2)
                price = c.number_input('Price ($)', min_value=0.01, value=30.0, step=1.0)
                notes = st.text_input('Notes (optional)', placeholder='e.g. light sleeve wear, original inner')
                if st.form_submit_button('Add copy', type='primary'):
                    if call(api.add_copy, record['id'], media, sleeve, f'{price:.2f}', notes.strip(),
                            success='Copy added (wantlists checked)'):
                        st.rerun()

with new_tab, st.form('new-record', clear_on_submit=True):
    a, b = st.columns(2)
    artist, title = a.text_input('Artist'), b.text_input('Title')
    c, d, e = st.columns(3)
    label, catalog_number, country = c.text_input('Label'), d.text_input('Catalog no.'), e.text_input('Country')
    f, g, h = st.columns(3)
    release_year = f.number_input('Year of this pressing', 1900, 2100, 1973)
    original_year = g.number_input('Year first released', 1900, 2100, 1973)
    fmt = h.selectbox('Format', FORMATS)
    genres = st.multiselect('Genres', ['jazz', 'soul', 'funk', 'rock', 'electronic', 'hip-hop', 'classical'],
                            accept_new_options=True)
    styles = st.multiselect('Styles', [], accept_new_options=True, placeholder='e.g. hard bop, jazz-funk')
    description = st.text_area('Description (in the shop\'s own words; the assistant uses it)')
    tracks = st.text_area('Tracklist, one per line: "A1 Chameleon"', height=120)
    if st.form_submit_button('Create record', type='primary'):
        tracklist = [{'position': p, 'title': t} for p, _, t in
                     (line.strip().partition(' ') for line in tracks.splitlines() if line.strip()) if t]
        record = {'artist': artist.strip(), 'title': title.strip(), 'label': label.strip(),
                  'catalog_number': catalog_number.strip() or None, 'country': country.strip() or None,
                  'release_year': int(release_year), 'original_year': int(original_year), 'format': fmt,
                  'genres': genres, 'styles': styles, 'description': description.strip(), 'tracklist': tracklist}
        if created := call(api.create_record, record):
            st.success(f'Created record #{created["id"]}. Add its copies in the first tab.')
