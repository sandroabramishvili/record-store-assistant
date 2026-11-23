from app.db import SessionLocal
from app.models import WantlistItem

COPY = {'media_condition': 'NM', 'sleeve_condition': 'VG+', 'price': '35.00'}
RECORD = {'artist': 'Kraftwerk', 'title': 'Autobahn', 'label': 'Philips', 'release_year': 1974,
          'original_year': 1974, 'genres': ['electronic'], 'description': 'Test release.'}


async def test_staff_only_inventory_crud(client, customer, staff):
    """A customer cannot call POST /inventory or POST /records."""
    assert (await client.post('/records', headers=customer.headers, json=RECORD)).status_code == 403
    record_id = (await client.post('/records', headers=staff.headers, json=RECORD)).json()['id']

    assert (await client.post('/inventory', headers=customer.headers,
                              json={'record_id': record_id, **COPY})).status_code == 403
    r = await client.post('/inventory', headers=staff.headers, json={'record_id': record_id, **COPY})
    assert r.status_code == 201
    item = r.json()['id']

    # The rest of the copy's lifecycle is staff-only too.
    for method, body in (('put', COPY), ('patch', {'status': 'sold'}), ('delete', None)):
        kwargs = {'json': body} if body else {}
        assert (await client.request(method, f'/inventory/{item}', headers=customer.headers, **kwargs)).status_code == 403
    assert (await client.delete(f'/inventory/{item}', headers=staff.headers)).status_code == 204


async def test_reserved_copy_cannot_be_deleted(client, customer, staff, add_copy):
    item = await add_copy()
    await client.post('/reservations', headers=customer.headers, json={'inventory_item_id': item})
    assert (await client.delete(f'/inventory/{item}', headers=staff.headers)).status_code == 409
    assert (await client.patch(f'/inventory/{item}', headers=staff.headers, json={'status': 'sold'})).status_code == 409


async def test_wantlist_match_on_new_inventory(client, customer, staff, add_record):
    """Adding a matching copy via POST /inventory sets matched_item_id on the wantlist entry."""
    entry = (await client.post('/wantlist', headers=customer.headers, json={
        'artist': 'kraftwerk', 'title': 'AUTOBAHN', 'max_price': '40', 'min_condition': 'VG+'})).json()
    assert entry['matched_item_id'] is None
    record_id = await add_record(artist='Kraftwerk', title='Autobahn')

    # A copy below the wanted condition does not match ...
    await client.post('/inventory', headers=staff.headers, json={'record_id': record_id, **COPY, 'media_condition': 'VG'})
    async with SessionLocal() as db:
        assert (await db.get(WantlistItem, entry['id'])).matched_item_id is None

    # ... a fitting one does, stored in the database by POST /inventory itself.
    item = (await client.post('/inventory', headers=staff.headers, json={'record_id': record_id, **COPY})).json()['id']
    async with SessionLocal() as db:
        assert (await db.get(WantlistItem, entry['id'])).matched_item_id == item

    body = (await client.get('/wantlist', headers=customer.headers)).json()
    assert body['match_count'] == 1
    assert body['items'][0]['match']['price'] == '35.00'


async def test_sold_match_is_cleared(client, customer, staff, add_record):
    await client.post('/wantlist', headers=customer.headers, json={'artist': 'Kraftwerk'})
    record_id = await add_record(artist='Kraftwerk', title='Autobahn')
    item = (await client.post('/inventory', headers=staff.headers, json={'record_id': record_id, **COPY})).json()['id']
    assert (await client.get('/wantlist', headers=customer.headers)).json()['match_count'] == 1

    await client.patch(f'/inventory/{item}', headers=staff.headers, json={'status': 'sold'})
    assert (await client.get('/wantlist', headers=customer.headers)).json()['match_count'] == 0
