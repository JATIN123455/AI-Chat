import os


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


class Settings:
    """All configuration comes from environment variables (read lazily so tests can override)."""

    @property
    def api_keys(self) -> list[str]:
        raw = os.getenv("LLM_API_KEYS", "")
        return [k.strip() for k in raw.split(",") if k.strip()]

    @property
    def database_url(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./app.db")

    @property
    def db_path(self) -> str:
        url = self.database_url
        prefix = "sqlite:///"
        return url[len(prefix):] if url.startswith(prefix) else url

    @property
    def allowed_origins(self) -> list[str]:
        raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def simulation_enabled(self) -> bool:
        return _bool("ENABLE_FAILURE_SIMULATION")

    @property
    def model(self) -> str:
        return os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")

    @property
    def default_system_prompt(self) -> str:
        return os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant.")

    @property
    def request_timeout(self) -> float:
        return float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


settings = Settings()
