"""
Step 4: FastAPI backend + static web frontend.

Endpoints
  GET  /              chat UI (static/index.html)
  GET  /api/health    liveness + index statistics
  POST /api/chat      {"message": str, "history": [{"role","content"}]} -> answer + sources
  POST /api/search    {"message": str} -> raw retrieval results (for debugging / demo)

Run:  uvicorn app:app --reload --port 8080
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config as C
from rag import RAG

app = FastAPI(title="THD Website Knowledge Assistant", version="1.0")
rag = RAG()


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[Message] = []


@app.get("/api/health")
def health():
    return {"status": "ok", **rag.stats()}


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        return rag.answer(req.message.strip(), [m.model_dump() for m in req.history])
    except Exception as e:
        raise HTTPException(500, f"Failed to answer: {e}")


@app.post("/api/search")
def search(req: ChatRequest):
    return {"results": rag.retrieve(req.message.strip())}


app.mount("/static", StaticFiles(directory=C.BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(C.BASE_DIR / "static" / "index.html")
