"""Small pieces shared by the pages: the error guard, record cards, grade options."""

import logging
from collections.abc import Callable
from typing import Any

import streamlit as st

from api_client import ApiError, friendly_message

logger = logging.getLogger('ui')

CONDITIONS = ['M', 'NM', 'VG+', 'VG', 'G+', 'G', 'F', 'P']          # best first, as dealers list them
CONDITION_HELP = 'Goldmine grading: M mint, NM near mint, VG+ very good plus, VG very good, G good, F fair, P poor.'


def call(fn: Callable, *args, success: str | None = None, **kwargs) -> Any:
    """Run one API call; on failure show a clear st.error (never a traceback) and return None.
    A call that succeeds without a body (DELETE → 204) returns True, so `if call(...)` works."""
    try:
        result = fn(*args, **kwargs)
    except ApiError as error:
        if error.status == 401:
            # Drop the login HERE too (not only in api_client): rerunning with the token still
            # set would hit the same 401 and rerun forever.
            for key in ('token', 'me'):
                st.session_state.pop(key, None)
            st.session_state['flash'] = friendly_message(error)
            st.rerun()                                   # → app.py shows the login page with the message
        st.error(friendly_message(error))
        return None
    if success:
        st.toast(success)
    return True if result is None else result


def money(value: Any) -> str:
    return f'${float(value):,.2f}' if value not in (None, '') else '—'


def pressing(record: dict) -> str:
    return f'{record["release_year"]} reissue' if record.get('is_reissue') else 'original'


def record_card(item: dict, *, number: int | None = None, show_status: bool = True) -> None:
    """A copy as a card (chat answers, wantlist matches): artist, title, year, pressing, grades, price."""
    with st.container(border=True):
        head = f'**{number}. ' if number else '**'
        st.markdown(f'{head}{item["artist"]} — {item["title"]}**')
        details = [str(item.get('year') or ''), item.get('pressing') or '',
                   f'{item["media_condition"]}/{item["sleeve_condition"]}']
        st.caption(' · '.join(d for d in details if d))
        left, right = st.columns([3, 1])
        right.markdown(f'**{money(item["price"])}**')
        status = item.get('status')
        if show_status and status and status != 'in_stock':
            left.caption(f':orange[{status.replace("_", " ")}]')
