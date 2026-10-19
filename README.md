# THD Website Knowledge Assistant

A Retrieval-Augmented Generation (RAG) chatbot that answers students' questions in natural language
using content scraped from the Deggendorf Institute of Technology website (https://www.th-deg.de/en).

Unofficial student internship prototype. The website remains the authoritative source.

## Architecture

```
 th-deg.de pages + PDFs
        │  crawler.py   BFS crawl · robots.txt · retries/backoff · language detection
        ▼
 data/raw_pages.jsonl  +  data/crawl_state.sqlite3   (resumable crawl state)
        │  indexer.py   dedupe · boilerplate removal · paragraph-aware chunking
        ▼
 ChromaDB (dense vectors, all-MiniLM-L6-v2)   +   chunks.jsonl (BM25 keyword index)
        │  rag.py       hybrid retrieval → Reciprocal Rank Fusion → top-k diverse passages
        ▼
 Grounded prompt (numbered sources) → Groq LLM (openai/gpt-oss-120b) → answer with [n] citations
        │  app.py       FastAPI REST API
        ▼
 static/  Chat UI (HTML/CSS/JS): suggestions, citations, source cards, follow-up questions
```

| Component | File | Key ideas |
|---|---|---|
| Crawler | `crawler.py` | Host scoping, URL normalization, `<html lang>` + langdetect language detection (many English pages have no `/en/` prefix), PDF text with PyMuPDF, SQLite state, resume re-seeds frontier from stored links |
| Indexer | `indexer.py` | URL + content-hash dedup, frequency-based boilerplate removal, ~900-char chunks with overlap, page title prepended to each chunk, **incremental** (only new chunks are embedded; `--rebuild` for a full rebuild) |
| Retriever | `rag.py` | Hybrid search: semantic (ChromaDB cosine) + lexical BM25 on text + BM25 on title/URL (field boosting), merged by weighted Reciprocal Rank Fusion; intent-aware re-ranking (job pages down-weighted for student questions); max 3 chunks per page |
| Generator | `rag.py` | LLM query rewriting (standalone, keyword-rich queries, resolves follow-ups); strict grounding prompt; [n] citations; "not found" behaviour; extractive fallback without an API key |
| API | `app.py` | `POST /api/chat`, `POST /api/search`, `GET /api/health`; Swagger docs at `/docs` |
| Frontend | `static/` | Chat UI with suggested questions, clickable citations, collapsible source list, saved chat history (browser localStorage) with open/delete |
| Evaluation | `evaluate.py` | Hit@5 and MRR on a curated question set |

## Setup and run (Windows PowerShell)

```powershell
cd thd-chatbot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env        # then paste your free Groq key from https://console.groq.com/keys

.\.venv\Scripts\python.exe crawler.py --fresh   # 1. scrape the website   (~5 min)
.\.venv\Scripts\python.exe indexer.py           # 2. build the vector index (~2 min)
.\.venv\Scripts\python.exe evaluate.py          # 3. (optional) retrieval metrics
.\run.ps1                                        # 4. open http://localhost:8080
```

Without a `GROQ_API_KEY` the app still runs in **extractive mode**: it shows the best retrieved
passages instead of a generated answer.

## Limitations and next steps
- English content only by default (`ALLOWED_LANGS` in `config.py`); add `"de"` and a multilingual embedding model for German.
- Scanned PDFs are detected (`no_text`) but not OCR'd.
- Crawl is capped (`MAX_HTML_PAGES`, `MAX_PDFS`) for demo speed; no incremental re-crawl yet.
- Possible improvements: cross-encoder re-ranking, streaming responses, answer-quality evaluation (LLM-as-judge), feedback buttons.
