"""Record Store Assistant: Streamlit UI (spec 10).

    streamlit run ui/app.py            (API_URL defaults to http://localhost:8000)

Navigation depends on the role from GET /auth/me: customers never SEE staff pages, and the
API refuses staff endpoints for them anyway (403); the UI hiding them is a convenience only.
"""

import logging

import streamlit as st

import api_client as api

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
st.set_page_config(page_title='Record Store Assistant', page_icon='💿', layout='wide')

CUSTOMER_PAGES = [
    st.Page('views/chat.py', title='Chat', icon='💬', default=True),
    st.Page('views/catalog.py', title='Browse catalog', icon='🔎'),
    st.Page('views/collection.py', title='My collection', icon='📚'),
    st.Page('views/wantlist.py', title='Wantlist', icon='⭐'),
    st.Page('views/reservations.py', title='My reservations', icon='🧾'),
    st.Page('views/profile.py', title='Profile', icon='👤'),
]
STAFF_PAGES = [
    st.Page('views/staff_inventory.py', title='Inventory', icon='📦'),
    st.Page('views/staff_reservations.py', title='All reservations', icon='📋'),
    st.Page('views/staff_knowledge.py', title='Knowledge base', icon='📖'),
    st.Page('views/staff_analytics.py', title='Analytics', icon='📊'),
]

if 'token' not in st.session_state:
    navigation = st.navigation([st.Page('views/login.py', title='Log in', icon='🔑')])
else:
    me = st.session_state['me']
    pages = {'Shop': CUSTOMER_PAGES}
    if me['role'] == 'staff':
        pages['Staff'] = STAFF_PAGES
    navigation = st.navigation(pages)
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
