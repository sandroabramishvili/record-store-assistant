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

    # Gemini
    google_api_key: str
    chat_model: str = 'gemini-3.5-flash'
    embedding_model: str = 'models/gemini-embedding-001'

    # Observability
    langsmith_project: str = 'record-store-assistant'
    environment: str = 'dev'

    @property
    def psycopg_url(self) -> str:
        """Connection string for PGVector (langchain-postgres uses psycopg 3)."""
        return self.database_url.replace('+asyncpg', '+psycopg')

    @property
    def checkpointer_url(self) -> str:
        """Connection string for AsyncPostgresSaver (plain libpq URL, no driver suffix)."""
        return self.database_url.replace('+asyncpg', '')


settings = Settings()
