"""Streamlit UI (Phase 16), run headless with streamlit.testing.AppTest.

No API server: the functions of ui/api_client.py are replaced by a fake. That is the point
of having ONE api client: the pages only ever talk to it. Checked here: role-based pages,
the chat (hint, history cards, Confirm / Cancel), friendly errors, and that no page ever
shows an exception (spec 10: no tracebacks).
"""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

UI = Path(__file__).resolve().parents[2] / 'ui'
sys.path.insert(0, str(UI))

import api_client  # noqa: E402
from api_client import ApiError, friendly_message  # noqa: E402

CUSTOMER = {'id': 2, 'email': 'jazz.fan@example.com', 'full_name': 'Jazz Fan', 'role': 'customer'}
STAFF = {'id': 1, 'email': 'staff@example.com', 'full_name': 'Store Staff', 'role': 'staff'}
CARD = {'id': 57, 'artist': 'Herbie Hancock', 'title': 'Head Hunters', 'year': 1973, 'pressing': 'original',
        'media_condition': 'VG+', 'sleeve_condition': 'VG', 'price': '38.00', 'status': 'in_stock'}
RECORD = {'id': 7, 'artist': 'Herbie Hancock', 'title': 'Head Hunters', 'label': 'Columbia', 'catalog_number': 'KC 32731',
          'release_year': 1973, 'original_year': 1973, 'country': 'US', 'format': 'LP', 'is_reissue': False,
          'genres': ['jazz'], 'styles': ['jazz-funk'], 'available_copies': 1, 'lowest_price': '38.00'}
COPY = {'id': 57, 'record_id': 7, 'media_condition': 'VG+', 'sleeve_condition': 'VG', 'price': '38.00',
        'status': 'in_stock', 'notes': None, 'added_at': '2026-11-01T10:00:00'}
RESERVATION = {'id': 3, 'user_id': 2, 'status': 'active', 'expires_at': '2026-11-25T18:00:00', 'created_at': '2026-11-23T18:00:00',
               'inventory_item_id': 57, 'price': '38.00', 'media_condition': 'VG+', 'sleeve_condition': 'VG',
               'record_id': 7, 'artist': 'Herbie Hancock', 'title': 'Head Hunters'}


class FakeApi:
    """Canned answers for every api_client function; `fail[name] = ApiError(...)` makes one fail."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.fail: dict[str, ApiError] = {}
        self.profile = {'favorite_genres': ['jazz'], 'disliked_genres': [], 'budget_cap': '60.00', 'min_condition': 'VG+'}
        self.collection = {'items': [], 'stats': {'total': 0, 'in_catalog': 0, 'by_genre': {}, 'by_decade': {}}}
        self.history = {'session': {'id': 1, 'title': 'Jazz-funk', 'created_at': '', 'updated_at': ''},
                        'messages': [], 'pending_confirmation': None}
        self.chats = [{'id': 1, 'title': 'Jazz-funk', 'created_at': '', 'updated_at': ''}]
        self.events = [{'type': 'node', 'name': 'understand_request'}, {'type': 'answer', 'message': 'Hi', 'items': []},
                       {'type': 'done'}]

    def _do(self, name, result, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.fail:
            raise self.fail[name]
        return result

    def install(self, monkeypatch):
        canned = {
            'get_profile': lambda: self.profile, 'get_collection': lambda: self.collection,
            'list_chats': lambda: {'items': self.chats, 'total': len(self.chats), 'page': 1, 'size': 50},
            'chat_history': lambda: self.history, 'new_chat': lambda: {'id': 2, 'title': 'New chat'},
            'list_records': lambda: {'items': [RECORD], 'total': 1, 'page': 1, 'size': 20},
            'get_record': lambda: RECORD | {'description': 'Classic.', 'tracklist': [], 'created_at': '', 'copies': [COPY]},
            'get_wantlist': lambda: {'items': [{'id': 1, 'artist': 'Herbie Hancock', 'title': 'Head Hunters',
                                                'max_price': None, 'min_condition': None, 'matched_item_id': 57,
                                                'created_at': '', 'match': {'item_id': 57, 'record_id': 7,
                                                'artist': 'Herbie Hancock', 'title': 'Head Hunters', 'price': '38.00',
                                                'media_condition': 'VG+', 'sleeve_condition': 'VG'}}],
                                     'match_count': 1},
            'list_reservations': lambda: {'items': [RESERVATION], 'total': 1, 'page': 1, 'size': 50},
            'reserve': lambda: RESERVATION, 'list_articles': lambda: [],
            'analytics': lambda: {'days': 30, 'searches': 3, 'genres': [{'name': 'jazz', 'count': 2}], 'artists': [],
                                  'titles': [], 'decades': [], 'zero_result_searches': 1, 'zero_result_rate': 0.333,
                                  'recent': [], 'most_wanted': [], 'created': 2, 'active': 1, 'completed': 1,
                                  'cancelled': 0, 'expired': 0, 'conversion_rate': 0.5, 'completion_rate_of_closed': 1.0,
                                  'total': 3, 'intents': [{'name': 'find_record', 'count': 3, 'share': 1.0}]},
        }
        for name, make in canned.items():
            monkeypatch.setattr(api_client, name, lambda *a, _n=name, _m=make, **k: self._do(_n, _m(), *a, **k))
        for name in ('send_message', 'confirm'):
            monkeypatch.setattr(api_client, name, lambda *a, _n=name, **k: self._stream(_n, *a, **k))
        for name in ('update_profile', 'add_to_collection', 'remove_from_collection', 'add_to_wantlist',
                     'remove_from_wantlist', 'cancel_reservation', 'complete_reservation', 'delete_chat'):
            monkeypatch.setattr(api_client, name, lambda *a, _n=name, **k: self._do(_n, None, *a, **k))
        return self

    def _stream(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.fail:
            raise self.fail[name]
        yield from self.events


@pytest.fixture
def api(monkeypatch):
    return FakeApi().install(monkeypatch)


def app(user: dict | None = None, page: str | None = None) -> AppTest:
    at = AppTest.from_file(str(UI / 'app.py'), default_timeout=20)
    if user:
        at.session_state['token'] = 'test-token'
        at.session_state['me'] = user
    if page:
        at.switch_page(page)
    return at.run()


def texts(at: AppTest) -> str:
    parts = [e.value for kind in ('title', 'markdown', 'caption', 'error', 'info', 'warning', 'success', 'subheader')
             for e in getattr(at, kind)]
    return '\n'.join(str(p) for p in parts)


# ---------- login & roles ----------

def test_login_page_when_logged_out_and_wrong_password_is_a_clear_message(api, monkeypatch):
    def bad_login(email, password):
        raise ApiError(401, 'Incorrect email or password')
    monkeypatch.setattr(api_client, 'login', bad_login)
    at = app()
    assert at.title[0].value.endswith('Record Store Assistant') and not at.exception
    at.text_input[0].input('jazz.fan@example.com')
    at.text_input[1].input('nope')
    at.button[0].click().run()
    assert [e.value for e in at.error] == ['Wrong email or password.'] and not at.exception


@pytest.mark.parametrize('page', ['views/staff_inventory.py', 'views/staff_reservations.py',
                                  'views/staff_knowledge.py', 'views/staff_analytics.py'])
def test_customer_cannot_open_staff_pages(api, page):
    """The page isn't registered for a customer: switching to it shows the default page (Chat).
    (The API refuses staff endpoints with 403 anyway: tests/api.)"""
    at = app(CUSTOMER, page)
    assert not at.exception
    assert at.title[0].value == '💬 Ask the shop'
    assert not any(name == 'analytics' for name, *_ in api.calls)


@pytest.mark.parametrize('page, title', [('views/staff_inventory.py', '📦 Inventory'),
                                         ('views/staff_analytics.py', '📊 Analytics')])
def test_staff_can_open_staff_pages(api, page, title):
    at = app(STAFF, page)
    assert at.title[0].value == title and not at.exception


ALL_PAGES = ['views/chat.py', 'views/catalog.py', 'views/collection.py', 'views/wantlist.py', 'views/reservations.py',
             'views/profile.py', 'views/staff_inventory.py', 'views/staff_reservations.py', 'views/staff_knowledge.py',
             'views/staff_analytics.py']


@pytest.mark.parametrize('page', ALL_PAGES)
def test_every_page_renders_without_an_exception(api, page):
    assert not app(STAFF, page).exception


@pytest.mark.parametrize('page', ALL_PAGES)
def test_server_down_is_a_message_not_a_traceback(api, monkeypatch, page):
    down = ApiError(0, 'Cannot reach the store server at http://localhost:8000.')
    api.fail = {name: down for name in ('get_profile', 'get_collection', 'list_chats', 'chat_history', 'list_records',
                                        'get_wantlist', 'list_reservations', 'list_articles', 'analytics')}
    at = app(STAFF, page)
    assert not at.exception
    if api.calls:                                    # (Inventory calls nothing until you search)
        assert any('Cannot reach the store server' in e.value for e in at.error)


def test_expired_login_goes_back_to_the_login_page(api):
    api.fail['list_chats'] = ApiError(401, 'Not authenticated')
    at = app(CUSTOMER)
    assert at.title[0].value.endswith('Record Store Assistant')                  # the login page
    assert 'token' not in at.session_state
    assert 'Your session has expired' in texts(at) and not at.exception
    assert 'Your session has expired' in texts(at) and not at.exception


# ---------- chat ----------

def test_hint_when_profile_and_collection_are_empty(api):
    api.profile['favorite_genres'] = []
    assert "so the assistant can recommend records you don't have yet" in texts(app(CUSTOMER))


def test_no_hint_when_the_customer_has_told_us_their_taste(api):
    assert 'so the assistant can recommend' not in texts(app(CUSTOMER))


def test_history_shows_numbered_cards_and_pending_confirmation(api):
    api.history['messages'] = [
        {'id': 1, 'role': 'user', 'content': '70s jazz-funk', 'items': [], 'created_at': ''},
        {'id': 2, 'role': 'assistant', 'content': 'Two picks.', 'items': [CARD, CARD | {'id': 58, 'status': 'reserved'}],
         'created_at': ''},
    ]
    api.history['pending_confirmation'] = {'type': 'confirmation_required', 'action': 'reserve', 'hold_hours': 48,
                                           'question': 'Reserve Herbie Hancock — Head Hunters for 48 hours?'}
    at = app(CUSTOMER)
    shown = texts(at)
    assert '**1. Herbie Hancock — Head Hunters**' in shown and '**2. Herbie Hancock — Head Hunters**' in shown
    assert ':orange[reserved]' in shown                                # current status of a card
    assert 'Reserve Herbie Hancock — Head Hunters for 48 hours?' in shown
    assert at.chat_input[0].disabled                                   # answer the question first (API: 409)

    next(b for b in at.button if b.label == 'Confirm').click().run()
    assert ('confirm', (1, True), {}) in api.calls and not at.exception


def test_sending_a_message_streams_and_reloads(api):
    at = app(CUSTOMER)
    at.chat_input[0].set_value('Any Coltrane?').run()
    assert ('send_message', (1, 'Any Coltrane?'), {}) in api.calls and not at.exception


def test_rate_limited_message_shows_the_reason(api):
    api.fail['send_message'] = ApiError(429, 'You have sent 30 messages in the last hour. Please try again a little later.',
                                        retry_after=540)
    at = app(CUSTOMER)
    at.chat_input[0].set_value('one more').run()
    assert any('30 messages in the last hour' in e.value and '9 min' in e.value for e in at.error)
    assert not at.exception


# ---------- reservations from the UI ----------

def test_reserving_a_copy_someone_just_took_says_so(api):
    api.fail['reserve'] = ApiError(409, 'That copy is no longer available', 'ReservationConflict')
    at = app(CUSTOMER, 'views/wantlist.py')
    next(b for b in at.button if b.label == 'Reserve this copy').click().run()
    assert [e.value for e in at.error] == ['Sorry, someone just reserved that copy.'] and not at.exception


def test_friendly_messages():
    assert friendly_message(ApiError(409, 'x', 'ReservationConflict')) == 'Sorry, someone just reserved that copy.'
    assert friendly_message(ApiError(409, 'You already have 3 active reservations.', 'ReservationLimitReached')) == \
        'You already have 3 active reservations.'
    assert friendly_message(ApiError(403, 'Staff only')) == 'Sorry, that is only available to staff.'
    assert friendly_message(ApiError(404, 'Not found')).startswith('Not found')
    assert friendly_message(ApiError(503, 'boom')).startswith('Something went wrong on the server')
    assert 'content' in friendly_message(ApiError(422, 'content: String should have at most 1000 characters'))
