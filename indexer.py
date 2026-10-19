"""
Step 2 of the pipeline: clean, chunk and index the crawled records.

Data-quality steps (see BRD section 10.5):
  1. Deduplicate records by URL and by content hash.
  2. Remove boilerplate lines that repeat across many pages (menus, footers, banners).
  3. Drop records that are too short or not in an allowed language.
Then split each record into overlapping, paragraph-aware chunks and store them in
ChromaDB (dense vectors) and chunks.jsonl (used for BM25 keyword search).

Usage:  python indexer.py             (incremental: embed only new chunks)
        python indexer.py --rebuild   (drop and rebuild the whole collection)
"""
import hashlib
import json
import re
from collections import Counter

import chromadb
from chromadb.utils import embedding_functions

import config as C


def load_records() -> list[dict]:
    recs, seen_url, seen_hash = [], set(), set()
    with open(C.RAW_PAGES, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            h = hashlib.md5(r["text"].encode()).hexdigest()
            if r["url"] in seen_url or h in seen_hash or r.get("lang") not in C.ALLOWED_LANGS:
                continue
            seen_url.add(r["url"])
            seen_hash.add(h)
            if "Webseite" in r["title"] or not r["title"].strip():   # generic title -> use first line
                first = next((ln for ln in r["text"].splitlines() if len(ln.strip()) > 3), r["url"])
                r["title"] = first.strip()[:90]
            recs.append(r)
    return recs


def strip_boilerplate(recs: list[dict]) -> None:
    """Remove lines that appear on >25% of HTML pages (navigation, footer, cookie text)."""
    html = [r for r in recs if r["type"] == "html"]
    freq = Counter(ln for r in html for ln in set(r["text"].splitlines()) if ln.strip())
    limit = max(5, int(len(html) * 0.25))
    boiler = {ln for ln, n in freq.items() if n >= limit}
    for r in html:
        r["text"] = "\n".join(ln for ln in r["text"].splitlines() if ln not in boiler).strip()
    print(f"Removed {len(boiler)} boilerplate lines")


def chunk_text(text: str, size=C.CHUNK_CHARS, overlap=C.CHUNK_OVERLAP) -> list[str]:
    """Pack paragraphs (then sentences) into ~size-char chunks with a small overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces = []
    for p in paras:
        p = re.sub(r"\n+", " \n", p)
        if len(p) <= size:
            pieces.append(p)
        else:
            pieces.extend(s for s in re.split(r"(?<=[.!?])\s+", p) if s)
    chunks, cur = [], ""
    for piece in pieces:
        while len(piece) > size:                       # very long sentence / table row
            chunks.append(piece[:size])
            piece = piece[size - overlap:]
        if len(cur) + len(piece) + 1 > size and cur:
            chunks.append(cur.strip())
            cur = cur[-overlap:].split(" ", 1)[-1] + " " + piece   # overlap on word boundary
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) > 60]


def build(rebuild: bool = False):
    recs = load_records()
    print(f"Loaded {len(recs)} unique records")
    strip_boilerplate(recs)
    recs = [r for r in recs if len(r["text"]) > 150]

    chunks = []
    for r in recs:
        for i, body in enumerate(chunk_text(r["text"])):
            cid = hashlib.md5(f"{r['url']}#{i}".encode()).hexdigest()
            chunks.append({
                "id": cid, "url": r["url"], "title": r["title"], "type": r["type"],
                "lang": r["lang"], "chunk": i,
                # Title is prepended so each chunk carries its page context into the embedding.
                "text": f"{r['title']}\n{body}",
            })
    print(f"Created {len(chunks)} chunks from {len(recs)} records")

    with open(C.CHUNKS_FILE, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    client = chromadb.PersistentClient(path=str(C.CHROMA_DIR))
    if rebuild:
        try:
            client.delete_collection(C.COLLECTION)
        except Exception:
            pass
    col = client.get_or_create_collection(
        C.COLLECTION,
        embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    # Incremental indexing: only embed chunks that are not in the collection yet,
    # and remove chunks whose source page no longer produces them.
    existing = set(col.get(include=[])["ids"])
    wanted = {c["id"] for c in chunks}
    stale = list(existing - wanted)
    for i in range(0, len(stale), 500):
        col.delete(ids=stale[i:i + 500])
    new = [c for c in chunks if c["id"] not in existing]
    print(f"Existing {len(existing)}, new {len(new)}, stale removed {len(stale)}")

    B = 128
    for i in range(0, len(new), B):
        b = new[i:i + B]
        col.add(ids=[c["id"] for c in b], documents=[c["text"] for c in b],
                metadatas=[{k: c[k] for k in ("url", "title", "type", "lang", "chunk")} for c in b])
        print(f"  indexed {min(i + B, len(new))}/{len(new)}", flush=True)
    print(f"Done. Collection '{C.COLLECTION}' has {col.count()} chunks.")


if __name__ == "__main__":
    import sys
    build(rebuild="--rebuild" in sys.argv)
