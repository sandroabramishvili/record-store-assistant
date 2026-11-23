"""Pure helpers of the chat endpoints: cards, SSE lines, titles, error messages."""

import json

from app.ai.chat import answer_cards, error_message, make_config, sse
from app.models import User
from app.routers.chat import title_from
from tests.graph.fakes import copy_row


def test_cards_follow_shown_order_and_only_this_turns_copies():
    values = {
        'available_items': [copy_row(1, 10, 'Herbie Hancock', 'Head Hunters', price='38.00'),
                            copy_row(2, 11, 'Donald Byrd', 'Black Byrd', release_year=2016)],
        'shown_item_ids': [2, 1, 99],           # 99: from an earlier turn, not in this turn's data
    }
    cards = answer_cards(values)
    assert [c['id'] for c in cards] == [2, 1]
    assert cards[0]['pressing'] == '2016 reissue' and cards[1]['pressing'] == 'original'
    assert cards[1]['price'] == '38.00'


def test_nothing_new_featured_means_no_cards():
    """A reserve turn keeps the old shown_item_ids for "the second one", but shows no cards."""
    assert answer_cards({'available_items': [], 'shown_item_ids': [5, 6]}) == []


def test_cards_from_catalog_details_for_record_info():
    values = {'catalog_details': [{'artist': 'John Coltrane', 'title': 'Blue Train', 'original_year': 1957,
                                   'release_year': 2016, 'pressing': 'reissue',
                                   'copies_in_stock': [{'id': 7, 'media_condition': 'NM', 'sleeve_condition': 'NM',
                                                        'price': '32.00'}]},
                                  {'collection_stats': {}}],
              'shown_item_ids': [7]}
    [card] = answer_cards(values)
    assert (card['id'], card['year'], card['pressing']) == (7, 1957, '2016 reissue')


def test_sse_line_format():
    line = sse({'type': 'done'})
    assert line.startswith('data: ') and line.endswith('\n\n')
    assert json.loads(line[6:]) == {'type': 'done'}


def test_config_has_thread_and_no_email():
    user = User(id=3, email='alice@example.com', role='customer')
    config = make_config(12, user)
    assert config['configurable']['thread_id'] == 'chat-12'
    assert 'alice@example.com' not in json.dumps(config, default=str)   # no emails in traces
    assert make_config(12, user)['run_id'] != config['run_id']          # a new root run per request


def test_title_is_shortened_and_single_line():
    assert title_from('70s   jazz-funk\nunder $40') == '70s jazz-funk under $40'
    long = title_from('x' * 100)
    assert len(long) == 60 and long.endswith('…')


def test_error_messages():
    assert 'busy' in error_message(RuntimeError('429 RESOURCE_EXHAUSTED'), {})
    assert 'busy' in error_message(RuntimeError("503 UNAVAILABLE. {'message': 'high demand'}"), {})
    assert 'took too long' in error_message(TimeoutError(), {})
    assert 'unavailable' in error_message(ConnectionError(), {})
    done = error_message(TimeoutError(), {'action_result': {'status': 'reserved', 'label': 'Head Hunters'}})
    assert done.startswith('Your reservation of Head Hunters is confirmed')
