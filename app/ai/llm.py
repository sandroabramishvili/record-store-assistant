"""The chat model (Gemini) and its two structured-output wrappers.

The graph never parses free text from the model: it asks for a Pydantic object
(`with_structured_output`) and gets a validated `RequestAnalysis` / `AnswerDraft`,
or an exception. Tests replace the model with a fake (Phase 12), so nothing here
is imported by the pure business-logic modules.

No `temperature`: newer Gemini models (e.g. gemini-3.5-flash-lite) use fixed sampling
and reject custom values; Gemini 3 is tuned for its defaults anyway.
"""

from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError

from app.ai.schemas import AnswerDraft, RequestAnalysis
from app.config import settings

STRUCTURED_ATTEMPTS = 3   # invalid JSON / failed validation / no object → ask again
INVALID_OUTPUT_ERRORS = (OutputParserException, ValidationError)


@lru_cache
def get_chat_model() -> BaseChatModel:
    kwargs = {
        'model': settings.chat_model,
        'google_api_key': settings.gemini_api_key(),
        'timeout': settings.llm_timeout_seconds,
        'max_retries': 2,          # network-level retries inside the client
    }
    if settings.chat_thinking_level:
        kwargs['thinking_level'] = settings.chat_thinking_level
    return ChatGoogleGenerativeAI(**kwargs)


def _require_output(result):
    """Some models answer with plain text instead of the requested object; the parser then
    returns None. Treat that like any other invalid output (so it is retried)."""
    if result is None:
        raise OutputParserException('the model returned no structured output')
    return result


def structured(llm: BaseChatModel, schema: type) -> Runnable:
    """messages → a validated `schema` object.

    Retried (3 attempts) ONLY for invalid output: a broken or empty object is often fixed by
    asking again. Network problems (timeouts, 429) are NOT retried here: the client already
    retries them (`max_retries`), and stacking both would make a customer wait minutes.
    After the last attempt INVALID_OUTPUT_ERRORS reach the node, which has a fallback.
    """
    return (llm.with_structured_output(schema) | RunnableLambda(_require_output)).with_retry(
        retry_if_exception_type=INVALID_OUTPUT_ERRORS, stop_after_attempt=STRUCTURED_ATTEMPTS)


def analyzer(llm: BaseChatModel) -> Runnable:
    """messages → RequestAnalysis (understand_request node)."""
    return structured(llm, RequestAnalysis)


def composer(llm: BaseChatModel) -> Runnable:
    """messages → AnswerDraft (compose_answer node)."""
    return structured(llm, AnswerDraft)
