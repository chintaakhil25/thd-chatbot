# THD Website Knowledge Assistant

A Retrieval-Augmented Generation (RAG) chatbot that answers students' questions about the
Deggendorf Institute of Technology in natural language, using content scraped from
https://www.th-deg.de/en. Every answer is grounded in website content and cites its sources.

> Unofficial student internship prototype. The website remains the authoritative source.

---

## Quick start (about 10 minutes)

These steps are for **Windows** (PowerShell). macOS/Linux commands are given where they differ.

### What you need first

| Requirement | How to check / get it |
|---|---|
| **Python 3.10 or newer** | Run `python --version`. If missing, install from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during setup. |
| **Git** | Run `git --version`. If missing: https://git-scm.com/downloads |
| **A free Groq API key** | Sign up at https://console.groq.com/keys → **Create API Key** → copy it (starts with `gsk_`). |
| **Access to this repo** | Accept the collaborator invite email from GitHub if the repo is private. |

### Step 1: Download the code

Open PowerShell in the folder where you want the project (e.g. your Desktop) and run:

```powershell
git clone https://github.com/chintaakhil25/thd-chatbot.git
cd thd-chatbot
```

### Step 2: Create a virtual environment and install the libraries

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This takes 2–5 minutes. *(macOS/Linux: `python3 -m venv .venv` then `.venv/bin/python -m pip install -r requirements.txt`.)*

### Step 3: Download the pre-built knowledge base

The scraped and indexed website data is **not** stored in the code (it is ~120 MB). Download it instead:

1. Open the repo on GitHub → **Releases** (right-hand side) → latest release → download **`thd-index.zip`**.
2. Move `thd-index.zip` into the `thd-chatbot` folder and run:

```powershell
Expand-Archive thd-index.zip -DestinationPath data
```

*(macOS/Linux: `unzip thd-index.zip -d data`)*

Check that you now have **`data\chroma_v2\`** (a folder) and **`data\chunks.jsonl`** (a file).

> No zip available? You can build the knowledge base yourself instead; see
> [Rebuilding the knowledge base](#rebuilding-the-knowledge-base-optional) (takes ~40 minutes).

### Step 4: Add your Groq API key

```powershell
copy .env.example .env
notepad .env
```

In Notepad, paste your key directly after the `=` (no spaces, no quotes), then save:

```
GROQ_API_KEY=gsk_your_key_here
```

> Never share or commit the `.env` file. It is already excluded by `.gitignore`.

### Step 5: Start the app

```powershell
.\run.ps1
```

If PowerShell says *"running scripts is disabled on this system"*, use this instead:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8080
```

*(macOS/Linux: `.venv/bin/python -m uvicorn app:app --port 8080`)*

Wait until you see `Uvicorn running on http://127.0.0.1:8080`, then open **http://localhost:8080**.
Keep the terminal open while using the app; press **Ctrl+C** to stop it.

### Step 6: Try it out

| Try this | What you should see |
|---|---|
| Click **"How do I apply for a master's programme?"** | A written answer with **[1] [2]** citations; click **Sources** to see the THD pages used |
| Ask *"How can I apply for the summer semester?"* | Application window **15 October – 15 January** and the steps |
| Then ask *"what documents do I need for that?"* | A follow-up answer that understands "that" from the previous question |
| Ask *"What is the canteen menu on Mars?"* | It says it couldn't find this on the THD website (it doesn't make things up) |
| Click **+ New chat**, then click the earlier chat in **Recent chats** | The old conversation reopens (chats are saved in your browser) |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` is not recognized | Reinstall Python and tick **"Add python.exe to PATH"**, or use `py` instead of `python`. |
| Error mentioning `Collection [thd_knowledge] does not exist` or `chunks.jsonl` not found | Step 3 was skipped or unzipped to the wrong place. You need `data\chroma_v2\` and `data\chunks.jsonl`. |
| Answers are just raw text passages, not written answers | The API key isn't loaded. Check the file is named `.env` (not `.env.txt`), the key has no spaces/quotes, then restart the app. |
| Answer starts with *"The language model is unavailable right now (AuthenticationError)"* | The Groq key is wrong or was deleted. Create a new one and update `.env`. |
| *"(RateLimitError)"* | The free Groq tier has per-minute limits. Wait a few seconds and ask again. |
| `address already in use` / port error | Another program uses port 8080. Run with `--port 8090` and open http://localhost:8090. |
| Page looks old after an update | Press **Ctrl+F5** in the browser. |

---

## How it works

```
 th-deg.de pages + PDFs
        │  crawler.py   BFS crawl · robots.txt · retries/backoff · language detection
        ▼
 data/raw_pages.jsonl  +  data/crawl_state.sqlite3   (resumable crawl state)
        │  indexer.py   dedupe · boilerplate removal · paragraph-aware chunking
        ▼
 ChromaDB (dense vectors, all-MiniLM-L6-v2)   +   chunks.jsonl (BM25 keyword index)
        │  rag.py       query rewriting → hybrid retrieval → weighted Reciprocal Rank Fusion
        ▼
 Grounded prompt (numbered sources) → Groq LLM (openai/gpt-oss-120b) → answer with [n] citations
        │  app.py       FastAPI REST API
        ▼
 static/  Chat UI (HTML/CSS/JS): saved chat history, citations, collapsible sources
```

| Component | File | Key ideas |
|---|---|---|
| Crawler | `crawler.py` | Host scoping, URL normalization, `<html lang>` + langdetect language detection (many English pages have no `/en/` prefix), PDF text with PyMuPDF, SQLite state, resume re-seeds frontier from stored links |
| Indexer | `indexer.py` | URL + content-hash dedup, frequency-based boilerplate removal, ~900-char chunks with overlap, page title prepended to each chunk, **incremental** (only new chunks are embedded; `--rebuild` for a full rebuild) |
| Retriever | `rag.py` | Hybrid search: semantic (ChromaDB cosine) + lexical BM25 on text + BM25 on title/URL (field boosting), merged by weighted Reciprocal Rank Fusion; intent-aware re-ranking (job pages down-weighted for student questions); max 3 chunks per page |
| Generator | `rag.py` | LLM query rewriting (standalone, keyword-rich queries, resolves follow-ups); strict grounding prompt; [n] citations; "not found" behaviour; extractive fallback without an API key |
| API | `app.py` | `POST /api/chat`, `POST /api/search`, `GET /api/health`; interactive API docs at http://localhost:8080/docs |
| Frontend | `static/` | Chat UI with suggested questions, clickable citations, collapsible source list, saved chat history (browser localStorage) with open/delete |
| Evaluation | `evaluate.py` | Hit@5 and MRR on a curated question set |
| Settings | `config.py` | Crawl limits, chunk sizes, retrieval parameters, model name |

### Measuring retrieval quality

```powershell
.\.venv\Scripts\python.exe evaluate.py
```

Prints whether the right page is found in the top 5 results for 15 test questions
(current result: **Hit@5 = 100%, MRR = 0.90**). Does not need an API key.

---

## Rebuilding the knowledge base (optional)

Only needed if you don't have `thd-index.zip` or want fresh website data. Requires an internet connection.

```powershell
.\.venv\Scripts\python.exe crawler.py --fresh   # scrape ~2,500 pages + 100 PDFs  (~10–15 min)
.\.venv\Scripts\python.exe indexer.py           # chunk + embed into ChromaDB      (~25–30 min)
```

- The crawler can be stopped with Ctrl+C and resumed by running `crawler.py` again (without `--fresh`).
- Running `indexer.py` again later only embeds new content; use `indexer.py --rebuild` to start over.
- Crawl limits can be changed in `config.py` (`MAX_HTML_PAGES`, `MAX_PDFS`).

## Limitations and next steps

- English content only by default (`ALLOWED_LANGS` in `config.py`); add `"de"` and a multilingual embedding model for German.
- Not the entire website: the crawl is capped (2,500 pages, 100 PDFs) for speed; many unvisited pages are staff profiles and news.
- Scanned PDFs are detected (`no_text`) but not OCR'd.
- Chat history is stored per browser (not synced across devices); no user accounts.
- Possible improvements: cross-encoder re-ranking, streaming responses, answer-quality evaluation (LLM-as-judge), feedback buttons, scheduled re-crawls.
