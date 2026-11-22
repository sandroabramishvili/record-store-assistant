"""Chat with the assistant (spec 10): history, live node progress, record cards, Confirm / Cancel.

The server is the source of truth: after every turn the page reloads the history from
GET /chat/sessions/{id}/messages (answers, cards with CURRENT stock status, and a question
still waiting for an answer, which survives a page reload or an API restart).
"""

from collections.abc import Iterator

import streamlit as st

import api_client as api
from api_client import ApiError
from components import call, record_card

HINT = ('Tell us what you like and add a few records you own, '
        "so the assistant can recommend records you don't have yet.")

NODE_LABELS = {                                    # what each graph step means to a customer
    'understand_request': 'Reading your message',
    'load_context': 'Checking your profile and collection',
    'search_inventory': 'Searching the shelves',
    'availability_filter': 'Checking stock, condition and budget',
    'relax_constraints': 'Nothing exact; widening the search',
    'offer_wantlist': 'Preparing a wantlist suggestion',
    'retrieve_knowledge': "Looking it up in the shop's guides",
    'collection_insights': 'Comparing with your collection',
    'prepare_reservation': 'Checking that copy is still free',
    'prepare_wantlist_entry': 'Preparing your wantlist entry',
    'confirm_action': 'Got your answer',
    'execute_action': 'Updating our records',
    'compose_answer': 'Writing the answer',
    'verify_answer': 'Double-checking prices and stock',
    'fallback_template': 'Putting together a plain answer',
    'polite_refusal': 'Answering',
}


def run_turn(events: Iterator[dict], user_text: str | None = None) -> None:
    """Show a turn while it streams; then reload the page from the server's history."""
    if user_text:
        with st.chat_message('user'):
            st.markdown(user_text)
    with st.chat_message('assistant'):
        with st.status('Working on it…', expanded=False) as status:
            try:
                for event in events:
                    if event['type'] == 'node':
                        label = NODE_LABELS.get(event['name'], event['name'])
                        status.update(label=f'{label}…')
                        st.write(f'✓ {label}')
                    elif event['type'] == 'error':
                        status.update(label='The assistant ran into a problem', state='error')
                    elif event['type'] in ('answer', 'confirmation_required'):
                        status.update(label='Done', state='complete')
            except ApiError as error:                 # 409 (pending / still answering), 429, network…
                status.update(label='Not sent', state='error')
                st.error(api.friendly_message(error))
                return                                # keep the message on screen; no reload
    st.rerun()                                        # the answer / question / error is in the history now


def show_hint() -> None:
    if st.session_state['me']['role'] != 'customer':
        return
    profile, collection = call(api.get_profile), call(api.get_collection)
    if profile is not None and collection is not None and not profile['favorite_genres'] \
            and collection['stats']['total'] == 0:
        st.info(HINT, icon='💡')


# ---------- the chat list (sidebar) ----------

chats = (call(api.list_chats) or {'items': []})['items']
chat_ids = [c['id'] for c in chats]
if st.session_state.get('chat_id') not in chat_ids:
    st.session_state['chat_id'] = chat_ids[0] if chat_ids else None
current = st.session_state['chat_id']

with st.sidebar:
    st.subheader('Your chats')
    if st.button('➕ New chat', width='stretch'):
        if created := call(api.new_chat):
            st.session_state['chat_id'] = created['id']
            st.rerun()
    for chat in chats:
        if st.button(chat['title'], key=f'chat-{chat["id"]}', width='stretch',
                     type='primary' if chat['id'] == current else 'secondary'):
            st.session_state['chat_id'] = chat['id']
            st.rerun()

# ---------- the conversation ----------

title_col, delete_col = st.columns([5, 1])
title_col.title('💬 Ask the shop')
if current and delete_col.button('Delete chat', help='Deletes this chat and its history'):
    if call(api.delete_chat, current):
        st.session_state['chat_id'] = None
        st.rerun()

show_hint()

pending = None
if current:
    history = call(api.chat_history, current)
    if history:
        for message in history['messages']:
            with st.chat_message(message['role']):
                st.markdown(message['content'])
                many = len(message['items']) > 1
                for n, item in enumerate(message['items'], start=1):
                    record_card(item, number=n if many else None)
        pending = history['pending_confirmation']
else:
    st.caption('Ask for a record ("70s jazz-funk under $40, at least VG+"), about vinyl ("what does VG+ mean?"), '
               'or about your collection ("which Radiohead albums am I missing?").')

if pending:
    with st.container(border=True):
        st.markdown(f'**{pending["question"]}**')
        if pending.get('action') == 'reserve':
            st.caption(f'Held for {pending.get("hold_hours", 48)} hours; pay and collect in the shop.')
        yes, no, _ = st.columns([1, 1, 4])
        if yes.button('Confirm', type='primary', width='stretch'):
            run_turn(api.confirm(current, True))
        if no.button('Cancel', width='stretch'):
            run_turn(api.confirm(current, False))

prompt = st.chat_input('Please answer the question above first' if pending else 'Ask about records…',
                       disabled=bool(pending), max_chars=1000)
if prompt:
    if current is None:                                # first message: the chat is created on the fly
        created = call(api.new_chat)
        if created is None:
            st.stop()
        st.session_state['chat_id'] = current = created['id']
    run_turn(api.send_message(current, prompt), user_text=prompt)
