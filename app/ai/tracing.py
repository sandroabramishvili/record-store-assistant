"""LangSmith tracing (spec 9, guide 13).

Every chat turn becomes one trace: the root run `chat_turn`, one child per graph node,
the two LLM calls inside. Turned on by LANGSMITH_TRACING=true + LANGSMITH_API_KEY in .env.

Why an explicit tracer (and not LangChain's automatic one):
- the automatic tracer reads os.environ, but .env is read by pydantic-settings only, so
  locally it would never switch on;
- it uses a default client WITHOUT the anonymizer. Ours is passed in the run config's
  callbacks; LangChain then adds no second (env) tracer, so there are no duplicate runs.

Personal data: the graph only gets user_id, never an email, password or token. But a
customer can TYPE an email (or paste a token) into the chat, so the client anonymizes every
run's inputs and outputs before they leave the process.
"""

import logging
from functools import lru_cache
from uuid import UUID

from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client
from langsmith.anonymizer import create_anonymizer

from app.config import settings

logger = logging.getLogger(__name__)

# Order matters: a JWT or key is replaced before the more general patterns see it.
REDACTIONS = [
    {'pattern': r'eyJ[\w-]+\.[\w-]+\.[\w-]+', 'replace': '<jwt>'},                  # JSON Web Token
    {'pattern': r'(?i)bearer\s+[\w.~+/=-]+', 'replace': 'Bearer <token>'},
    {'pattern': r'AIza[\w-]{35}', 'replace': '<google-api-key>'},
    {'pattern': r'lsv2_[\w]+', 'replace': '<langsmith-key>'},
    {'pattern': r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', 'replace': '<email>'},
]
anonymize = create_anonymizer(REDACTIONS)


def tracing_enabled() -> bool:
    return settings.langsmith_tracing and bool(settings.langsmith_api_key)


@lru_cache
def get_client() -> Client | None:
    if not tracing_enabled():
        return None
    return Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint, anonymizer=anonymize)


def make_tracer(client: Client) -> LangChainTracer:
    return LangChainTracer(project_name=settings.langsmith_project, client=client)


def tracing_callbacks() -> list[LangChainTracer]:
    """For a run's config: [tracer] when tracing is on, else [] (then nothing is sent)."""
    client = get_client()
    return [make_tracer(client)] if client else []


def tag_run(run_id: UUID, tags: list[str]) -> None:
    """After a run: add what is only known at the end (the intent) to the root run as a TAG.

    `update_run` REPLACES the tags, so the caller passes the full list. Tags only: a real
    check showed LangSmith ignores a metadata update on a run that has already ended.
    Never lets a tracing problem break the chat.
    """
    client = get_client()
    if client is None:
        return
    try:
        client.update_run(run_id, tags=tags)
    except Exception:
        logger.warning('Could not tag LangSmith run %s', run_id, exc_info=True)


def flush_traces() -> None:
    """At shutdown: send what is still queued (the client batches in a background thread)."""
    client = get_client()
    if client is not None:
        client.flush()
