"""LiteLLM wrapper: key pool, automatic failover and built-in response caching."""
import logging
import time
from dataclasses import dataclass

import litellm
from litellm.caching.caching import Cache

from .config import settings

log = logging.getLogger("llm")
litellm.suppress_debug_info = True
litellm.cache = Cache(type="local")  # LiteLLM in-memory cache

SIMULATED_STATUSES = {429, 500, 502, 503}

# Tests can set e.g. {"mock_response": "hi"} to avoid real provider calls.
EXTRA_COMPLETION_KWARGS: dict = {}


class AllProvidersUnavailable(Exception):
    pass


class SimulatedProviderError(Exception):
    def __init__(self, status: int):
        super().__init__(f"simulated provider error {status}")
        self.status_code = status


@dataclass
class LLMResult:
    reply: str
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int

    def metadata(self) -> dict:
        return {
            "resolution_source": self.resolution_source,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "key_id": self.key_id,
            "fallback_attempts": self.fallback_attempts,
        }


def parse_simulation(header: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, ...}. Ignored unless enabled."""
    if not header or not settings.simulation_enabled:
        return {}
    result: dict[str, int] = {}
    for part in header.split(","):
        if "=" not in part:
            continue
        key, _, status = part.partition("=")
        key, status = key.strip(), status.strip()
        if status.isdigit() and int(status) in SIMULATED_STATUSES:
            result[key] = int(status)
    return result


def _usage(resp) -> tuple[int, int]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


def complete(messages: list[dict], simulate: dict[str, int] | None = None) -> LLMResult:
    simulate = simulate or {}
    keys = settings.api_keys
    model = settings.model
    start = time.perf_counter()

    # Simulated failures bypass the cache so failover is always demonstrable on demand.
    use_cache = not simulate
    failures = 0

    for idx, api_key in enumerate(keys, start=1):
        key_id = f"key_{idx}"
        try:
            status = simulate.get(key_id) or simulate.get("*")
            if status:
                raise SimulatedProviderError(status)

            resp = litellm.completion(
                model=model,
                messages=messages,
                api_key=api_key,
                timeout=settings.request_timeout,
                num_retries=0,
                caching=use_cache,
                **EXTRA_COMPLETION_KWARGS,
            )
            reply = (resp.choices[0].message.content or "").strip()
            if not reply:
                raise ValueError("empty completion")

            cache_hit = bool((getattr(resp, "_hidden_params", {}) or {}).get("cache_hit"))
            in_tok, out_tok = _usage(resp)
            return LLMResult(
                reply=reply,
                resolution_source="LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
                model=model,
                latency_ms=max(1, int((time.perf_counter() - start) * 1000)),
                input_tokens=in_tok,
                output_tokens=out_tok,
                key_id=None if cache_hit else key_id,
                fallback_attempts=0 if cache_hit else failures,
            )
        except Exception as exc:  # 429, quota, 5xx, auth, timeout... -> next key
            failures += 1
            log.warning("%s failed (%s: %s)", key_id, type(exc).__name__, str(exc)[:120])

    raise AllProvidersUnavailable()
