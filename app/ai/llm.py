"""The chat model (Gemini) and its two structured-output wrappers.

The graph never parses free text from the model: it asks for a Pydantic object
(`with_structured_output`) and gets a validated `RequestAnalysis` / `AnswerDraft`,
or an exception. Tests replace the model with a fake (Phase 12), so nothing here
is imported by the pure business-logic modules.

No `temperature`: newer Gemini models (e.g. gemini-3.5-flash-lite) use fixed sampling
and reject custom values; Gemini 3 is tuned for its defaults anyway.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI

from app.ai.schemas import AnswerDraft, RequestAnalysis
from app.config import settings

STRUCTURED_ATTEMPTS = 3   # invalid JSON / failed validation / transient error → try again


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


def analyzer(llm: BaseChatModel) -> Runnable:
    """messages → RequestAnalysis (understand_request node)."""
    return llm.with_structured_output(RequestAnalysis).with_retry(stop_after_attempt=STRUCTURED_ATTEMPTS)


def composer(llm: BaseChatModel) -> Runnable:
    """messages → AnswerDraft (compose_answer node)."""
    return llm.with_structured_output(AnswerDraft).with_retry(stop_after_attempt=STRUCTURED_ATTEMPTS)
