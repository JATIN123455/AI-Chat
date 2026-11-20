import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from . import db, llm
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # schema is created automatically on startup
    yield


app = FastAPI(title="AI Chat", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    system_prompt: Optional[Annotated[str, StringConstraints(max_length=4000)]] = None


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health")
def health():
    ok = db.ping() and len(settings.api_keys) > 0
    body = {
        "status": "ok" if ok else "degraded",
        "keys_configured": len(settings.api_keys),
        "cache_enabled": llm.litellm.cache is not None,
    }
    return JSONResponse(status_code=200 if ok else 503, content=body)


@app.post("/chat")
def chat(req: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
    started = time.perf_counter()
    history = db.get_history(req.session_id)
    stored_prompt = db.get_system_prompt(req.session_id)
    system_prompt = req.system_prompt if req.system_prompt is not None else stored_prompt
    effective_prompt = (system_prompt or "").strip() or settings.default_system_prompt

    messages = [{"role": "system", "content": effective_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": req.message})

    try:
        result = llm.complete(messages, llm.parse_simulation(x_simulate_failure))
    except llm.AllProvidersUnavailable:
        return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

    result.latency_ms = max(1, int((time.perf_counter() - started) * 1000))
    metadata = result.metadata()
    db.save_exchange(req.session_id, req.message, result.reply, metadata, req.system_prompt)
    return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str):
    messages = db.get_history(session_id)
    if not messages:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    return {"session_id": session_id, "messages": messages}
