"""Every call from the UI to the FastAPI backend lives here (one place, not per page).

The UI never touches the database or the graph. The JWT is kept in st.session_state and
sent with every request; the backend decides what the user may do (roles, ownership),
so hiding a page in the UI is a convenience, never the protection.

Errors: every failure becomes ApiError(status, message, code). Pages show
`friendly_message(error)` with st.error, never a traceback.
"""

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

def _api_url() -> str:
    """API_URL, with "localhost" pinned to IPv4. On Windows "localhost" resolves to ::1 first;
    uvicorn listens on 127.0.0.1 only, so EVERY request waited ~2 s for the IPv6 attempt to
    fail (measured: 2.27 s vs 0.02 s per call, i.e. 10-14 s per page)."""
    url = os.getenv('API_URL', 'http://127.0.0.1:8000').rstrip('/')
    return url.replace('://localhost:', '://127.0.0.1:').replace('://localhost/', '://127.0.0.1/')


API_URL = _api_url()
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# A chat turn may take up to the API's turn deadline (90 s) between the first and last event.
STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=15.0, pool=5.0)
# One shared client: connections are reused (keep-alive) instead of a new client, TCP
# connection and setup per call (~0.25 s each). Safe to share between Streamlit sessions.
_http = httpx.Client(base_url=API_URL, timeout=TIMEOUT)


class ApiError(Exception):
    """status 0 = the API could not be reached at all."""

    def __init__(self, status: int, message: str, code: str | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.retry_after = status, message, code, retry_after


# ---------- plumbing ----------


def _headers() -> dict[str, str]:
    token = st.session_state.get('token')
    return {'Authorization': f'Bearer {token}'} if token else {}


def _error_from(response: httpx.Response) -> ApiError:
    code, detail = None, response.text or response.reason_phrase
    try:
        body = response.json()
        detail, code = body.get('detail', detail), body.get('error')
    except (ValueError, AttributeError):
        pass
    if isinstance(detail, list):                       # 422: [{loc: [...], msg: ...}, ...]
        detail = '; '.join(f"{'.'.join(str(p) for p in d.get('loc', [])[1:]) or 'input'}: {d.get('msg')}"
                           for d in detail)
    retry = response.headers.get('Retry-After')
    return ApiError(response.status_code, str(detail), code, int(retry) if retry and retry.isdigit() else None)


def _forget_expired_login(error: ApiError) -> None:
    """401 on a logged-in call = the token expired (60 min) → back to the login page."""
    if error.status == 401 and st.session_state.get('token'):
        for key in ('token', 'me'):
            st.session_state.pop(key, None)


def request(method: str, path: str, **kwargs) -> Any:
    try:
        response = _http.request(method, path, headers=_headers(), **kwargs)
    except httpx.TimeoutException as e:
        raise ApiError(0, 'The store server took too long to respond.') from e
    except httpx.HTTPError as e:
        raise ApiError(0, f'Cannot reach the store server at {API_URL}.') from e
    if response.status_code >= 400:
        error = _error_from(response)
        _forget_expired_login(error)
        raise error
    return response.json() if response.content else None


def _stream(path: str, body: dict) -> Iterator[dict]:
    """POST that answers with Server-Sent Events: yields each `data: {...}` event as a dict."""
    try:
        with _http.stream('POST', path, json=body, headers=_headers(), timeout=STREAM_TIMEOUT) as r:
            if r.status_code >= 400:
                r.read()
                error = _error_from(r)
                _forget_expired_login(error)
                raise error
            for line in r.iter_lines():
                if line.startswith('data: '):
                    yield json.loads(line[6:])
    except httpx.TimeoutException as e:
        raise ApiError(0, 'The assistant took too long to answer.') from e
    except httpx.HTTPError as e:
        raise ApiError(0, f'Lost the connection to the store server at {API_URL}.') from e


def friendly_message(error: ApiError) -> str:
    """What the customer reads (clear messages for 401, 403, 404, 409, timeouts)."""
    if error.status == 0:
        return error.message
    if error.status == 401:
        return 'Your session has expired. Please log in again.'
    if error.status == 403:
        return 'Sorry, that is only available to staff.'
    if error.status == 404:
        return 'Not found. It may have been removed in the meantime.'
    if error.status == 409 and error.code == 'ReservationConflict':
        return 'Sorry, someone just reserved that copy.'
    if error.status == 422:
        return f'Please check your input. {error.message}'
    if error.status == 429:
        minutes = max(1, round((error.retry_after or 60) / 60))
        return f'{error.message} (about {minutes} min)'
    if error.status >= 500:
        return 'Something went wrong on the server. Please try again in a moment.'
    return error.message                              # 409s etc.: the API's messages are written for customers


# ---------- auth / profile ----------


def login(email: str, password: str) -> None:
    data = request('POST', '/auth/login', data={'username': email, 'password': password})
    st.session_state['token'] = data['access_token']
    st.session_state['me'] = request('GET', '/auth/me')


def register(email: str, full_name: str, password: str) -> None:
    request('POST', '/auth/register', json={'email': email, 'full_name': full_name, 'password': password})


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def get_profile() -> dict:
    return request('GET', '/profile')


def update_profile(profile: dict) -> dict:
    return request('PUT', '/profile', json=profile)


# ---------- catalog / inventory ----------


def list_records(**filters) -> dict:
    return request('GET', '/records', params={k: v for k, v in filters.items() if v not in (None, '', False)})


def get_record(record_id: int) -> dict:
    return request('GET', f'/records/{record_id}')


def create_record(record: dict) -> dict:
    return request('POST', '/records', json=record)


def add_copy(record_id: int, media: str, sleeve: str, price: str, notes: str | None = None) -> dict:
    return request('POST', '/inventory', json={'record_id': record_id, 'media_condition': media,
                                               'sleeve_condition': sleeve, 'price': price, 'notes': notes or None})


def update_copy(item_id: int, media: str, sleeve: str, price: str, notes: str | None = None) -> dict:
    return request('PUT', f'/inventory/{item_id}', json={'media_condition': media, 'sleeve_condition': sleeve,
                                                         'price': price, 'notes': notes or None})


def set_copy_status(item_id: int, status: str) -> dict:
    return request('PATCH', f'/inventory/{item_id}', json={'status': status})


def delete_copy(item_id: int) -> None:
    request('DELETE', f'/inventory/{item_id}')


# ---------- collection / wantlist / reservations ----------


def get_collection() -> dict:
    return request('GET', '/collection')


def add_to_collection(record_id: int | None = None, artist: str | None = None, title: str | None = None) -> dict:
    body = {'record_id': record_id} if record_id else {'artist': artist, 'title': title}
    return request('POST', '/collection', json=body)


def remove_from_collection(item_id: int) -> None:
    request('DELETE', f'/collection/{item_id}')


def get_wantlist() -> dict:
    return request('GET', '/wantlist')


def add_to_wantlist(artist: str, title: str | None, max_price: str | None, min_condition: str | None) -> dict:
    return request('POST', '/wantlist', json={'artist': artist, 'title': title or None,
                                              'max_price': max_price or None, 'min_condition': min_condition or None})


def remove_from_wantlist(item_id: int) -> None:
    request('DELETE', f'/wantlist/{item_id}')


def list_reservations(status: str | None = None, page: int = 1, size: int = 50) -> dict:
    return request('GET', '/reservations', params={k: v for k, v in
                                                   {'status': status, 'page': page, 'size': size}.items() if v})


def reserve(item_id: int) -> dict:
    """The catalog's "Reserve" button: the same reservation service the chat uses."""
    return request('POST', '/reservations', json={'inventory_item_id': item_id})


def cancel_reservation(reservation_id: int) -> dict:
    return request('DELETE', f'/reservations/{reservation_id}')


def complete_reservation(reservation_id: int) -> dict:
    return request('POST', f'/reservations/{reservation_id}/complete')


# ---------- chat ----------


def list_chats() -> dict:
    return request('GET', '/chat/sessions', params={'size': 50})


def new_chat() -> dict:
    return request('POST', '/chat/sessions')


def delete_chat(session_id: int) -> None:
    request('DELETE', f'/chat/sessions/{session_id}')


def chat_history(session_id: int) -> dict:
    return request('GET', f'/chat/sessions/{session_id}/messages')


def send_message(session_id: int, content: str) -> Iterator[dict]:
    return _stream(f'/chat/sessions/{session_id}/messages', {'content': content})


def confirm(session_id: int, approved: bool) -> Iterator[dict]:
    return _stream(f'/chat/sessions/{session_id}/confirm', {'approved': approved})


# ---------- staff: knowledge / analytics ----------


def list_articles() -> list[dict]:
    return request('GET', '/knowledge/articles')


def save_article(article: dict, article_id: int | None = None) -> dict:
    if article_id:
        return request('PUT', f'/knowledge/articles/{article_id}', json=article)
    return request('POST', '/knowledge/articles', json=article)


def delete_article(article_id: int) -> None:
    request('DELETE', f'/knowledge/articles/{article_id}')


def search_knowledge(query: str, k: int = 3) -> list[dict]:
    return request('POST', '/knowledge/search', json={'query': query, 'k': k})


def analytics(name: str, days: int, limit: int | None = None) -> dict:
    params = {'days': days} | ({'limit': limit} if limit else {})
    return request('GET', f'/analytics/{name}', params=params)
