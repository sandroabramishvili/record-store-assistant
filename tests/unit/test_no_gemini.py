"""Proof that the test suite never calls Gemini.

tests/conftest.py blanks GOOGLE_API_KEY for the whole run. So if any test (or code under
test) tried to build the real chat model or the real embeddings, it would fail right here,
with a clear error, instead of quietly making a paid, slow, non-deterministic call.
"""

import pytest

from app.ai import vectorstores
from app.ai.llm import get_chat_model
from app.config import settings


def test_the_suite_runs_without_a_gemini_key():
    assert settings.google_api_key == ''


def test_real_chat_model_cannot_be_built():
    get_chat_model.cache_clear()
    with pytest.raises(RuntimeError, match='GOOGLE_API_KEY is not set'):
        get_chat_model()


def test_real_embeddings_cannot_be_built():
    vectorstores._gemini_embeddings.cache_clear()
    with pytest.raises(RuntimeError, match='GOOGLE_API_KEY is not set'):
        vectorstores._gemini_embeddings()
