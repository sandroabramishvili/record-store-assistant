import streamlit as st

import api_client as api
from api_client import ApiError

st.title('💿 Record Store Assistant')

if flash := st.session_state.pop('flash', None):
    st.warning(flash)

log_in, sign_up = st.tabs(['Log in', 'Create an account'])

with log_in, st.form('login'):
    email = st.text_input('Email')
    password = st.text_input('Password', type='password')
    if st.form_submit_button('Log in', type='primary'):
        try:
            api.login(email.strip(), password)
            st.rerun()
        except ApiError as error:
            st.error('Wrong email or password.' if error.status in (401, 422) else api.friendly_message(error))

with sign_up, st.form('register'):
    name = st.text_input('Your name')
    new_email = st.text_input('Email ')
    new_password = st.text_input('Password (at least 8 characters)', type='password')
    if st.form_submit_button('Create account'):
        try:
            api.register(new_email.strip(), name.strip(), new_password)
            api.login(new_email.strip(), new_password)
            st.rerun()
        except ApiError as error:
            st.error(api.friendly_message(error) if error.status != 409 else 'That email is already registered.')
