"""Releases and articles keep their vectors in sync with the database (fake embeddings)."""

from tests.api.conftest import count_vectors

RECORD = {'artist': 'Herbie Hancock', 'title': 'Head Hunters', 'label': 'Columbia', 'release_year': 1973,
          'original_year': 1973, 'genres': ['jazz', 'funk'], 'description': 'Long hypnotic grooves.'}
ARTICLE = {'title': 'The Goldmine Grading Scale', 'topic': 'grading',
           'content': ' '.join(f'Sentence {i}: VG+ records may tick quietly in silent passages.' for i in range(60))}


async def test_release_vector_follows_create_update_delete(client, staff):
    record_id = (await client.post('/records', headers=staff.headers, json=RECORD)).json()['id']
    rows = await count_vectors('records')
    assert [r[0] for r in rows] == [f'record-{record_id}']
    assert 'hypnotic' in rows[0][1] and rows[0][2]['record_id'] == record_id

    # Editing a release updates its vector, same id → no duplicate.
    await client.put(f'/records/{record_id}', headers=staff.headers,
                     json=RECORD | {'description': 'Synth-heavy jazz-funk.', 'release_year': 2015})
    rows = await count_vectors('records')
    assert len(rows) == 1 and 'Synth-heavy' in rows[0][1] and rows[0][2]['is_reissue'] is True

    await client.delete(f'/records/{record_id}', headers=staff.headers)
    assert await count_vectors('records') == []


async def test_release_saved_even_if_embedding_fails(client, staff, monkeypatch):
    """The DB is the source of truth: a Gemini outage must not lose the new release."""
    from app.ai import vectorstores

    async def broken(*args, **kwargs):
        raise RuntimeError('embedding service down')
    monkeypatch.setattr(vectorstores.get_record_store(), 'aadd_documents', broken)

    r = await client.post('/records', headers=staff.headers, json=RECORD)
    assert r.status_code == 201
    assert (await client.get(f'/records/{r.json()["id"]}', headers=staff.headers)).status_code == 200


async def test_article_chunks_follow_create_update_unpublish_delete(client, staff):
    r = await client.post('/knowledge/articles', headers=staff.headers, json=ARTICLE)
    assert r.status_code == 201
    article = r.json()
    assert article['chunk_count'] >= 3
    rows = await count_vectors('knowledge')
    assert [x[0] for x in rows] == sorted(f'kb-{article["id"]}-{i}' for i in range(article['chunk_count']))
    assert all(x[1].startswith('The Goldmine Grading Scale') for x in rows)

    # Shorter content → fewer chunks, the old extra chunks are gone.
    short = ARTICLE | {'content': 'VG+ means light signs of play but no noticeable noise.'}
    article = (await client.put(f'/knowledge/articles/{article["id"]}', headers=staff.headers, json=short)).json()
    assert article['chunk_count'] == 1 and len(await count_vectors('knowledge')) == 1

    # Unpublished → no chunks: the assistant can't find it.
    hidden = (await client.put(f'/knowledge/articles/{article["id"]}', headers=staff.headers,
                               json=short | {'is_published': False})).json()
    assert hidden['chunk_count'] == 0 and await count_vectors('knowledge') == []

    await client.put(f'/knowledge/articles/{article["id"]}', headers=staff.headers, json=short)
    assert (await client.delete(f'/knowledge/articles/{article["id"]}', headers=staff.headers)).status_code == 204
    assert await count_vectors('knowledge') == []


async def test_knowledge_search_and_staff_only(client, staff, customer):
    content = 'VG+ means light signs of play but no noticeable noise.'
    await client.post('/knowledge/articles', headers=staff.headers, json=ARTICLE | {'content': content})
    await client.post('/knowledge/articles', headers=staff.headers,
                      json={'title': 'Storing Records', 'topic': 'care', 'content': 'Always store records upright and cool.'})

    hits = (await client.post('/knowledge/search', headers=staff.headers,
                              json={'query': f'The Goldmine Grading Scale\n\n{content}', 'k': 2})).json()
    assert hits[0]['title'] == 'The Goldmine Grading Scale'
    assert hits[0]['score'] > 0.99 > hits[1]['score']   # fake embeddings: identical text → similarity 1

    assert (await client.get('/knowledge/articles', headers=customer.headers)).status_code == 403
    assert (await client.post('/knowledge/search', headers=customer.headers, json={'query': 'x'})).status_code == 403
