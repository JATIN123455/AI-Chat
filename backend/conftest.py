import pytest
from fastapi.testclient import TestClient

from app import llm


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("LLM_API_KEYS", "fake-a,fake-b")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setattr(llm, "EXTRA_COMPLETION_KWARGS", {"mock_response": "The capital of France is Paris."})
    llm.litellm.cache = llm.Cache(type="local")  # fresh cache per test
    from app.main import app
    with TestClient(app) as c:
        yield c
