import os
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


class Settings:
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "dev-secret-change-me")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./email_analyser.db")
    google_client_secrets_file: str = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
    google_redirect_uri: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    gmail_max_results: int = int(os.getenv("GMAIL_MAX_RESULTS", "50"))
    ai_provider: str = os.getenv("AI_PROVIDER", "demo").lower()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


@lru_cache
def get_settings() -> Settings:
    return Settings()
