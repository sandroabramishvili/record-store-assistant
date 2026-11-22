import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import InventoryItem, Reservation


async def copy_status(item_id: int) -> str:
    async with SessionLocal() as db:
        return await db.scalar(select(InventoryItem.status).where(InventoryItem.id == item_id))


async def count_reservations(**where) -> int:
    query = select(func.count()).select_from(Reservation)
    for column, value in where.items():
        query = query.where(getattr(Reservation, column) == value)
    async with SessionLocal() as db:
        return await db.scalar(query)


async def reserve(client, user, item_id):
    return await client.post('/reservations', headers=user.headers, json={'inventory_item_id': item_id})


async def test_double_reservation_returns_409(client, customer, other_customer, add_copy):
    """Spec: reserving a copy that is already reserved → 409; only one reservation exists."""
    item = await add_copy()
    assert (await reserve(client, customer, item)).status_code == 201

    r = await reserve(client, other_customer, item)
    assert r.status_code == 409
    assert r.json()['error'] == 'ReservationConflict'
    assert 'someone just reserved' in r.json()['detail']
    assert await count_reservations(inventory_item_id=item) == 1
    assert await copy_status(item) == 'reserved'


async def test_parallel_reservations_exactly_one_wins(client, make_customers, add_copy):
    """Ten customers press "Reserve" on the same copy at the same instant."""
    item = await add_copy()
    customers = await make_customers(10)
    responses = await asyncio.gather(*(reserve(client, c, item) for c in customers))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 9
    assert await count_reservations(inventory_item_id=item, status='active') == 1


async def test_reservation_limit(client, customer, add_copy):
    """Spec: a 4th active reservation is rejected."""
    items = [await add_copy() for _ in range(4)]
    for item in items[:3]:
        assert (await reserve(client, customer, item)).status_code == 201

    r = await reserve(client, customer, items[3])
    assert r.status_code == 409
    assert r.json()['error'] == 'ReservationLimitReached'
    assert await copy_status(items[3]) == 'in_stock'       # nothing was written
    assert await count_reservations(user_id=customer.id) == 3


async def test_limit_holds_under_parallel_requests(client, customer, add_copy):
    """One customer fires 5 reservations at once: still only 3 (user-row lock)."""
    items = [await add_copy() for _ in range(5)]
    responses = await asyncio.gather(*(reserve(client, customer, i) for i in items))
    assert sorted(r.status_code for r in responses) == [201] * 3 + [409] * 2
    assert await count_reservations(user_id=customer.id, status='active') == 3


async def test_cancel_returns_copy_to_stock(client, customer, add_copy):
    item = await add_copy()
    res = (await reserve(client, customer, item)).json()

    r = await client.delete(f'/reservations/{res["id"]}', headers=customer.headers)
    assert r.status_code == 200 and r.json()['status'] == 'cancelled'
    assert await copy_status(item) == 'in_stock'
    assert (await client.delete(f'/reservations/{res["id"]}', headers=customer.headers)).status_code == 409


async def test_complete_is_staff_only_and_marks_sold(client, customer, staff, add_copy):
    item = await add_copy()
    res = (await reserve(client, customer, item)).json()

    assert (await client.post(f'/reservations/{res["id"]}/complete', headers=customer.headers)).status_code == 403
    r = await client.post(f'/reservations/{res["id"]}/complete', headers=staff.headers)
    assert r.status_code == 200 and r.json()['status'] == 'completed'
    assert await copy_status(item) == 'sold'


async def test_user_cannot_touch_others_reservation(client, customer, other_customer, add_copy):
    item = await add_copy()
    res = (await reserve(client, customer, item)).json()

    assert (await client.delete(f'/reservations/{res["id"]}', headers=other_customer.headers)).status_code == 404
    listed = (await client.get('/reservations', headers=other_customer.headers,
                               params={'user_id': customer.id})).json()
    assert listed['total'] == 0                              # user_id param ignored for customers


async def test_expired_reservation_is_released(client, customer, other_customer, add_copy):
    item = await add_copy()
    res = (await reserve(client, customer, item)).json()
    async with SessionLocal() as db:                         # pretend 48 hours have passed
        await db.execute(update(Reservation).where(Reservation.id == res['id'])
                         .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()

    assert (await reserve(client, other_customer, item)).status_code == 201   # copy is free again
    assert await count_reservations(id=res['id'], status='expired') == 1
