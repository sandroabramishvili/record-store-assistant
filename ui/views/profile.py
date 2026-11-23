"""Taste profile (GET/PUT /profile). The ONLY way to change it: the chat can't."""

import streamlit as st

import api_client as api
from components import CONDITION_HELP, CONDITIONS, call

GENRES = ['jazz', 'hard bop', 'jazz-funk', 'soul', 'funk', 'rock', 'pop rock', 'punk', 'post-punk', 'electronic',
          'hip-hop', 'classical', 'blues', 'reggae', 'folk', 'grunge', 'georgian']

st.title('Profile')
me = st.session_state['me']
st.caption(f'{me["full_name"]} · {me["email"]}')

profile = call(api.get_profile)
if profile:
    known = sorted(set(GENRES) | set(profile['favorite_genres']) | set(profile['disliked_genres']))
    with st.form('profile'):
        favorite = st.multiselect('Genres you love', known, default=profile['favorite_genres'], accept_new_options=True)
        disliked = st.multiselect("Genres you don't want recommended", known, default=profile['disliked_genres'],
                                  accept_new_options=True)
        a, b = st.columns(2)
        budget = a.number_input('Budget cap per record ($)', min_value=0.0, step=5.0,
                                value=float(profile['budget_cap'] or 0), help='0 = no cap. The assistant never shows dearer copies.')
        condition = b.selectbox('Lowest grade you accept', [None, *CONDITIONS], help=CONDITION_HELP,
                                index=([None, *CONDITIONS]).index(profile['min_condition']),
                                format_func=lambda x: 'Any grade' if x is None else x)
        if st.form_submit_button('Save', type='primary'):
            call(api.update_profile, {'favorite_genres': favorite, 'disliked_genres': disliked,
                                      'budget_cap': str(budget) if budget else None, 'min_condition': condition},
                 success='Profile saved')
