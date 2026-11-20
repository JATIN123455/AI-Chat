import uuid

from app import llm


def sid():
    return "s-" + uuid.uuid4().hex[:8]


def chat(client, session, message="What is the capital of France?", headers=None, **extra):
    return client.post("/chat", json={"session_id": session, "message": message, **extra}, headers=headers or {})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_chat_live_then_cache_in_new_session(client):
    first = chat(client, sid(), "Unique question one")
    assert first.status_code == 200
    m1 = first.json()["metadata"]
    assert m1["resolution_source"] == "LIVE_INFERENCE"
    assert m1["key_id"] == "key_1" and m1["fallback_attempts"] == 0
    assert m1["input_tokens"] > 0 and m1["output_tokens"] > 0

    second = chat(client, sid(), "Unique question one")
    m2 = second.json()["metadata"]
    assert m2["resolution_source"] == "LLM_CACHE"
    assert m2["key_id"] is None and m2["fallback_attempts"] == 0
    assert second.json()["reply"] == first.json()["reply"]
    assert m2["input_tokens"] == m1["input_tokens"]


def test_different_message_not_cached(client):
    chat(client, sid(), "alpha question")
    r = chat(client, sid(), "beta question")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_failover_key_1_429(client):
    r = chat(client, sid(), headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    meta = r.json()["metadata"]
    assert meta["key_id"] == "key_2" and meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"


def test_failover_key_1_500(client):
    r = chat(client, sid(), headers={"X-Simulate-Failure": "key_1=500"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_503_and_not_saved(client):
    s = sid()
    r = chat(client, s, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{s}/messages").status_code == 404


def test_simulation_ignored_when_disabled(client, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    r = chat(client, sid(), headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200


def test_real_provider_error_fails_over(client, monkeypatch):
    real = llm.litellm.completion
    calls = []

    def flaky(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == "fake-a":
            raise RuntimeError("429 quota exceeded")
        return real(**kwargs)

    monkeypatch.setattr(llm.litellm, "completion", flaky)
    r = chat(client, sid(), "provider error test")
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert calls == ["fake-a", "fake-b"]


def test_validation_422(client):
    assert client.post("/chat", json={"session_id": "x", "message": "   "}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "x", "message": "a" * 4001}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    bad = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert bad.status_code == 422 and "detail" in bad.json()


def test_multi_turn_history_and_separation(client):
    a, b = sid(), sid()
    chat(client, a, "first in A")
    chat(client, a, "second in A")
    chat(client, b, "only in B")

    hist = client.get(f"/sessions/{a}/messages").json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "user", "assistant"]
    assert hist["messages"][0]["content"] == "first in A"
    assert "metadata" in hist["messages"][1] and "metadata" not in hist["messages"][0]
    assert len(client.get(f"/sessions/{b}/messages").json()["messages"]) == 2


def test_history_is_sent_to_llm_and_tokens_are_per_request(client, monkeypatch):
    real = llm.litellm.completion
    sent = []

    def spy(**kwargs):
        sent.append(kwargs["messages"])
        return real(**kwargs)

    monkeypatch.setattr(llm.litellm, "completion", spy)
    s = sid()
    first = chat(client, s, "hello there").json()["metadata"]["input_tokens"]
    chat(client, s, "hello again")
    assert [m["role"] for m in sent[1]] == ["system", "user", "assistant", "user"]
    assert sent[1][1]["content"] == "hello there"
    # equal first messages in new sessions report the same per-request input tokens
    other = chat(client, sid(), "hello there").json()["metadata"]
    assert other["input_tokens"] == first


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_system_prompt_changes_cache_key(client):
    chat(client, sid(), "same msg", system_prompt="Be a pirate")
    r = chat(client, sid(), "same msg", system_prompt="Be a poet")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
