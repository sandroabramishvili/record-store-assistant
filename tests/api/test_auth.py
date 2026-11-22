from tests.api.conftest import PASSWORD


async def test_register_login_me(client):
    r = await client.post('/auth/register', json={
        'email': 'New@Example.com', 'full_name': 'New Customer', 'password': PASSWORD, 'role': 'staff'})
    assert r.status_code == 201
    assert r.json()['role'] == 'customer'               # role in the body is ignored
    assert 'hashed_password' not in r.json()

    token = (await client.post('/auth/login', data={'username': 'new@example.com', 'password': PASSWORD})).json()
    me = await client.get('/auth/me', headers={'Authorization': f'Bearer {token["access_token"]}'})
    assert me.status_code == 200 and me.json()['email'] == 'new@example.com'


async def test_login_does_not_reveal_registered_emails(client, customer):
    wrong_password = await client.post('/auth/login', data={'username': customer.email, 'password': 'nope-nope'})
    unknown_email = await client.post('/auth/login', data={'username': 'ghost@example.com', 'password': 'nope-nope'})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_requests_without_token_are_rejected(client):
    for path in ('/auth/me', '/profile', '/records', '/collection', '/wantlist', '/reservations'):
        assert (await client.get(path)).status_code == 401, path


async def test_user_cannot_delete_others_collection_item(client, customer, other_customer):
    item = (await client.post('/collection', headers=customer.headers,
                              json={'artist': 'The Beatles', 'title': 'Abbey Road'})).json()
    assert (await client.delete(f'/collection/{item["id"]}', headers=other_customer.headers)).status_code == 404
    assert (await client.delete(f'/collection/{item["id"]}', headers=customer.headers)).status_code == 204
