"""LangSmith tracing (Phase 13), checked offline.

A real langsmith Client with our anonymizer, but its HTTP session is replaced by one that
records what WOULD be sent. So these tests see exactly the bytes LangSmith would receive:
node-level runs, tags/metadata, and no email or token anywhere.
"""

import json
import os

import pytest
from langchain_core.callbacks.manager import CallbackManager
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import Client

from app.ai import tracing
from app.ai.chat import make_config, tag_with_intent
from app.ai.deps import Deps
from app.ai.graph import build_graph
from app.ai.schemas import RequestAnalysis, SearchConstraints
from app.ai.state import new_turn_input
from app.models import User
from tests.graph.fakes import FakeLLM, FakeRepo

EMAIL = 'jane.doe@example.com'
JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIyIn0.c2lnbmF0dXJlLXBhcnQ'


class _Response:
    status_code = 200
    headers: dict = {}
    text, content = '{}', b'{}'

    def raise_for_status(self):
        pass

    def json(self):
        return {}


class CapturingSession:
    """Stands in for requests.Session: records every request body, sends nothing."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def request(self, method, url, *args, **kwargs):
        body = kwargs.get('data') or kwargs.get('json') or b''
        self.sent.append((method, url, body.decode() if isinstance(body, bytes) else json.dumps(body)))
        return _Response()

    def mount(self, *args, **kwargs):
        pass

    def close(self):
        pass


@pytest.fixture
def langsmith(monkeypatch):
    session = CapturingSession()
    client = Client(api_key='test', api_url='http://langsmith.invalid', anonymizer=tracing.anonymize,
                    auto_batch_tracing=False, session=session, info={})
    monkeypatch.setattr(tracing, 'get_client', lambda: client)
    yield session, client


def sent_text(session) -> str:
    return '\n'.join(body for _, _, body in session.sent)


# ---------- the anonymizer itself ----------

def test_anonymizer_redacts_personal_data_and_secrets_in_nested_data():
    run = {'messages': [{'content': f'I am {EMAIL}, token Bearer abc.def-123 and {JWT}'}],
           'keys': ['AIza' + 'x' * 35, 'lsv2_pt_0123abcd'], 'price': '$38.00', 'genre': 'jazz-funk'}
    clean = json.dumps(tracing.anonymize(run))
    for secret in (EMAIL, JWT, 'abc.def-123', 'AIza' + 'x' * 35, 'lsv2_pt_0123abcd'):
        assert secret not in clean
    assert '<email>' in clean and '<jwt>' in clean and 'Bearer <token>' in clean
    assert '$38.00' in clean and 'jazz-funk' in clean                      # store data is left alone


def test_tracing_is_off_in_tests():
    assert tracing.get_client() is None and tracing.tracing_callbacks() == []


def test_our_tracer_is_the_only_one_even_with_env_tracing_on(monkeypatch):
    """With LANGSMITH_TRACING=true in the environment (e.g. Docker), LangChain would add its
    own tracer, without our anonymizer. It must not when ours is already in the callbacks."""
    monkeypatch.setitem(os.environ, 'LANGSMITH_TRACING', 'true')
    monkeypatch.setitem(os.environ, 'LANGSMITH_API_KEY', 'test')
    ours = tracing.make_tracer(Client(api_key='test', api_url='http://langsmith.invalid', session=CapturingSession(),
                                      info={}))
    manager = CallbackManager.configure(inheritable_callbacks=[ours])
    assert [h for h in manager.handlers if isinstance(h, LangChainTracer)] == [ours]


# ---------- a whole graph turn, as LangSmith would receive it ----------

async def test_a_chat_turn_is_traced_per_node_with_tags_and_no_email(langsmith):
    session, client = langsmith
    llm = FakeLLM(RequestAnalysis(intent='off_topic', constraints=SearchConstraints(query_text='x'),
                                  refers_to_previous=False, reference_index=None))
    graph = build_graph(Deps(llm=llm, repo=FakeRepo()), InMemorySaver())
    config = make_config(7, User(id=3, email=EMAIL, role='customer'))
    assert len(config['callbacks']) == 1

    state = await graph.ainvoke(new_turn_input(f'My email is {EMAIL}. Write me a Python script.', 3), config)
    tag_with_intent(config, state)
    client.flush()
    text = sent_text(session)

    assert EMAIL not in text and '<email>' in text                          # inputs anonymized
    for node in ('understand_request', 'polite_refusal'):                    # node-level detail
        assert f'"name":"{node}"' in text
    assert '"name":"chat_turn"' in text and str(config['run_id']) in text    # our root run
    assert '"session_id":7' in text and '"environment":"' in text            # metadata on runs

    method, url, body = session.sent[-1]                                     # the intent tag, added afterwards
    assert (method, url.rsplit('/', 1)[-1]) == ('PATCH', str(config['run_id']))
    assert '"intent:off_topic"' in body and '"chat"' in body                # full tag list (update replaces)
