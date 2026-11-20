# AI Chat: multi-turn chat with key failover, caching and token tracking

React + FastAPI + LiteLLM + SQLite, containerised and deployable.

## 1. Live URLs

- Frontend: `<add your deployed frontend URL>`
- Backend: `<add your deployed backend URL>` (try `/health`)
- Demo video: `<add link>`

## 2. Run locally

### With Docker
```bash
cp .env.example .env        # put at least 2 real keys in LLM_API_KEYS
make up                     # frontend http://localhost:3000, backend http://localhost:8000
make down
```

### Without Docker
```bash
# backend
cd backend && pip install -r requirements-dev.txt
export LLM_API_KEYS=key-a,key-b DATABASE_URL=sqlite:///./app.db ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
# frontend (new terminal)
cd frontend && npm install && VITE_API_URL=http://localhost:8000 npm run dev
# tests
cd backend && python -m pytest -q
```

## 3. Design decisions

**Key fallback.** `LLM_API_KEYS` is parsed into ids `key_1..key_n`. Each request tries the keys in order through `litellm.completion(api_key=...)`. Any 429, quota, 5xx, auth, timeout or empty-completion error moves on to the next key, and `fallback_attempts` counts the failures. If every key fails the API returns `503 {"detail": "all providers unavailable"}` and nothing is written to history. Keys are never logged or returned, only `key_id`.

**Caching.** LiteLLM's built-in in-memory cache (`Cache(type="local")`). The key is derived by LiteLLM from the model and the full message list (system prompt + history + new message), so the same first message in a new session hits the cache and any different message does not. A hit is detected from LiteLLM's `cache_hit` flag and reported as `LLM_CACHE` with `key_id: null`, the stored token usage and the same reply text. The cache is per process and is lost on restart.

**Failure simulation.** `X-Simulate-Failure` is honored only when `ENABLE_FAILURE_SIMULATION=true`. Listed keys raise a synthetic error before any provider call, so failover runs through the same code path as real errors. Simulated requests skip the cache so failover can always be demonstrated.

**SQLite session storage.** One `messages` table (the schema from the brief) plus an index and a small `sessions` table holding the per-session system prompt. The schema is created on startup, the file lives at `DATABASE_URL` (a Docker volume at `/data`), and WAL mode is on. The client only sends the new message. The server rebuilds context from SQLite. The user and assistant rows are inserted in one transaction only after a successful reply, so failed requests (422/503) never reach history.

**Latency and tokens.** `latency_ms` is measured server-side per request. Token counts come from the provider's usage for that request only (or the stored usage on a cache hit).

## 4. Verification

```bash
B=http://localhost:8000
curl $B/health
# live, then cache hit (new session, same first message)
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"a1","message":"What is the capital of France?"}'
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"a2","message":"What is the capital of France?"}'   # LLM_CACHE
# failover
curl -s $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"a3","message":"hi"}'   # key_id: key_2
curl -si $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"a4","message":"hi"}'      # 503
# history, validation
curl -s $B/sessions/a1/messages
curl -s $B/chat -H 'content-type: application/json' -d '{"session_id":"a5","message":"  "}'                                      # 422
```
In the UI, every reply shows source, model, latency and input/output tokens. The "Session settings" panel has a system prompt field and a failure-simulation field for demos.

## 5. Setup Used

`<your editor, e.g. VS Code>` and `<AI tools, e.g. Claude>`.

## 6. Bonus features

- UI field to send `X-Simulate-Failure` for live failover demos.
- Per-session system prompt, stored in SQLite and included in the cache key.
- Docker healthcheck, non-root backend image, catch-all JSON 500 handler (no stack traces).

## Deployment notes

- **Backend** (Render, Railway or Fly.io): use `backend/` as the root with its Dockerfile. Set `LLM_API_KEYS`, `LLM_MODEL`, `ALLOWED_ORIGINS=<frontend URL>`, `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL=sqlite:////data/app.db` and attach a persistent volume at `/data`. Free tiers with an ephemeral disk lose history on restart.
- **Frontend** (Vercel/Netlify/Cloudflare Pages): root `frontend/`, build `npm run build`, output `dist`, env `VITE_API_URL=<backend URL>`.
