"""Small pieces shared by the pages: the error guard, record cards, grade options."""

import logging
from collections.abc import Callable
from typing import Any

import streamlit as st

from api_client import ApiError, friendly_message
from style import esc

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


def record_card(item: dict, *, key: str, number: int | None = None, show_status: bool = True) -> None:
    """A copy as an LP-sleeve card (chat answers, wantlist matches): numbered "track" badge,
    artist — title, year · pressing · grades in typewriter, a price sticker, and a stamp
    when the copy is no longer in stock. `key` must be unique on the page (styling hook)."""
    with st.container(key=f'card-{key}'):
        left, right = st.columns([4, 1], vertical_alignment='center')
        badge = f'<span class="rs-track">{number}</span>' if number else ''
        details = [str(item.get('year') or ''), item.get('pressing') or '',
                   f'media {item["media_condition"]} / sleeve {item["sleeve_condition"]}']
        status = item.get('status')
        stamp = (f' &nbsp;<span class="rs-stamp">{esc(status.replace("_", " "))}</span>'
                 if show_status and status and status != 'in_stock' else '')
        left.markdown(f'{badge}<span class="rs-card-title">{esc(item["artist"])} — {esc(item["title"])}</span>{stamp}'
                      f'<br><span class="rs-card-meta">{esc(" · ".join(d for d in details if d))}</span>',
                      unsafe_allow_html=True)
        right.markdown(f'<span class="rs-price">{esc(money(item["price"]))}</span>', unsafe_allow_html=True)
