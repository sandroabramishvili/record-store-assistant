from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and the .env file.

    Environment variables take priority over values in .env, so Docker Compose
    can override e.g. DATABASE_URL without editing the file.
    """

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Database (SQLAlchemy async driver): postgresql+asyncpg://user:password@host:port/dbname
    database_url: str

    # Authentication
    jwt_secret: str
    jwt_expire_minutes: int = 60

    # Gemini. Optional at import time, so the app (and every test, migration, script that
    # needs no AI) works without it; building a real Gemini model without it fails clearly
    # (`gemini_api_key()`). The test suite blanks it on purpose (tests/conftest.py).
    google_api_key: str = ''
    chat_model: str = 'gemini-3.5-flash'
    # How much the model "thinks" before answering (Gemini 3+): minimal/low/medium/high.
    # Our tasks (classify a request, write a short answer from given data) need little;
    # lower = faster and cheaper. Empty = the model's default.
    chat_thinking_level: str | None = 'low'
    llm_timeout_seconds: float = 30
    embedding_model: str = 'models/gemini-embedding-001'
    # gemini-embedding-001 returns 3072 numbers by default; it can be shortened
    # (Matryoshka training) with little quality loss. 768 = 4x smaller, and below
    # pgvector's 2000-dimension limit for an HNSW index. Changing it requires re-indexing.
    embedding_dimensions: int = 768
    # Knowledge search (spec 8.9): chunks below this cosine similarity are ignored, so the
    # assistant says "I don't know" instead of answering from an unrelated article.
    # Measured on our articles: real questions ~0.75-0.80, off-topic ~0.48.
    knowledge_min_score: float = 0.6
    knowledge_top_k: int = 3

    # Observability
    langsmith_project: str = 'record-store-assistant'
    environment: str = 'dev'

    def gemini_api_key(self) -> str:
        """The key, for code that builds a real Gemini client. Missing → a clear error, not a 400 from Google."""
        if not self.google_api_key:
            raise RuntimeError('GOOGLE_API_KEY is not set: add it to .env to use Gemini (tests never need it).')
        return self.google_api_key

    @property
    def psycopg_url(self) -> str:
        """Connection string for PGVector (langchain-postgres uses psycopg 3)."""
        return self.database_url.replace('+asyncpg', '+psycopg')

    @property
    def checkpointer_url(self) -> str:
        """Connection string for AsyncPostgresSaver (plain libpq URL, no driver suffix)."""
        return self.database_url.replace('+asyncpg', '')


settings = Settings()
