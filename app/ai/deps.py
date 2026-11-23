"""Deps: the only way graph nodes reach the outside world.

Nodes get an LLM and a repository here and never create either themselves. In the
app, `build_deps()` wires the real Gemini model and the real database; tests pass a
fake LLM and a repository pointing at the test database.
"""

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from app.repositories.store import StoreRepository


@dataclass(frozen=True)
class Deps:
    llm: BaseChatModel          # Gemini in the app, a fake in tests
    repo: StoreRepository       # DB + vector stores


def build_deps() -> Deps:
    """Production wiring. Imported lazily so tests never construct the real model."""
    from app.ai.llm import get_chat_model
    from app.ai.vectorstores import get_knowledge_store, get_record_store
    from app.db import SessionLocal

    return Deps(
        llm=get_chat_model(),
        repo=StoreRepository(SessionLocal, get_record_store(), get_knowledge_store()),
    )
