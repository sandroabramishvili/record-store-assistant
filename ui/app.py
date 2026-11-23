"""Record Store Assistant: Streamlit UI (spec 10).

    streamlit run ui/app.py            (API_URL defaults to http://localhost:8000)

Navigation depends on the role from GET /auth/me: customers never SEE staff pages, and the
API refuses staff endpoints for them anyway (403); the UI hiding them is a convenience only.
"""

import logging

import streamlit as st

import api_client as api
import style

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
st.set_page_config(page_title='Record Store Assistant', page_icon=':material/album:', layout='wide')
style.apply()                                   # retro look: theme in config.toml + style.py

# Monochrome Material icons: they take the sidebar's cream text colour (emoji brought their own
# bright colours and fought the palette).
CUSTOMER_PAGES = [
    st.Page('views/chat.py', title='Chat', icon=':material/forum:', default=True),
    st.Page('views/catalog.py', title='Browse catalog', icon=':material/library_music:'),
    st.Page('views/collection.py', title='My collection', icon=':material/album:'),
    st.Page('views/wantlist.py', title='Wantlist', icon=':material/bookmark_heart:'),
    st.Page('views/reservations.py', title='My reservations', icon=':material/confirmation_number:'),
    st.Page('views/profile.py', title='Profile', icon=':material/person:'),
]
STAFF_PAGES = [
    st.Page('views/staff_inventory.py', title='Inventory', icon=':material/inventory_2:'),
    st.Page('views/staff_reservations.py', title='All reservations', icon=':material/receipt_long:'),
    st.Page('views/staff_knowledge.py', title='Knowledge base', icon=':material/menu_book:'),
    st.Page('views/staff_analytics.py', title='Analytics', icon=':material/bar_chart:'),
]

if 'token' not in st.session_state:
    navigation = st.navigation([st.Page('views/login.py', title='Log in', icon=':material/login:')])
else:
    me = st.session_state['me']
    pages = {'Shop': CUSTOMER_PAGES}
    if me['role'] == 'staff':
        pages['Staff'] = STAFF_PAGES
    navigation = st.navigation(pages)
    style.marquee()                             # the shop sign on top of every page
    with st.sidebar:
        st.caption(f'Logged in as **{me["full_name"]}** ({me["role"]})')
        if st.button('Log out', width='stretch'):
            api.logout()
            st.rerun()

try:
    navigation.run()
except Exception:                                        # last line of defence: never a traceback in the UI
    logging.getLogger('ui').exception('page failed')     # (st.rerun / st.stop are BaseException: not caught)
    st.error('Something went wrong on this page. Please reload it; if it keeps happening, tell the staff.')
