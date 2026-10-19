"""
Step 3: retrieval-augmented generation.

retrieve():  hybrid search = dense vectors (ChromaDB) + keyword BM25, merged with
             Reciprocal Rank Fusion, then diversified (max N chunks per page).
answer():    (optional) rewrites follow-up questions into standalone queries,
             builds a grounded prompt with numbered sources and calls the LLM.
             Without an API key it falls back to an extractive answer so the
             pipeline can still be demonstrated.
"""
import html
import json
import re
import time

import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi

import config as C

STOPWORDS = set("""a an the and or of to in on for with at by from is are was were be been do does did
how what when where which who whom why can could should would i me my we our you your it its this that
these those there as about into than then so if not no yes please tell know want need get have has""".split())


JOB_INTENT = re.compile(r"\b(job|jobs|vacanc\w*|professor\w*|career|position|employ\w*|work at|hiring|recruit\w*|lecturer)\b", re.I)
JOB_PAGE = re.compile(r"jobs|professor|application-process|stellen|vacanc|recruit", re.I)


def _stem(t: str) -> str:
    """Very light plural normalisation so 'applications' matches 'application'."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"\w+", text.lower()) if t not in STOPWORDS and len(t) > 1]


SYSTEM_PROMPT = """You are the THD Website Knowledge Assistant, an unofficial student-built helper for
Deggendorf Institute of Technology (Technische Hochschule Deggendorf, "DIT"/"THD").

Rules:
- Answer ONLY using the numbered context passages provided. Do not use outside knowledge.
- Cite the passages you used inline as [1], [2], etc. (plain square brackets, nothing else).
- Write a natural, helpful answer (like a knowledgeable student advisor), not a copy of the passages.
- DIT has several campuses (main campus in Deggendorf, plus e.g. Pfarrkirchen/ECRI, Cham and others).
  If a question could apply to more than one campus and the user did not specify, answer for the
  main Deggendorf campus first when the context covers it, and briefly mention the others found.
- Ignore passages that are about a different audience than the question (e.g. professor
  recruitment when the student asks about student applications).
- If the context does not contain the answer, say clearly that you could not find it on the THD
  website and suggest where the student could check (e.g. the relevant THD office or page). Do not guess.
- Be concise and friendly. Use short paragraphs or bullet points. Answer in the language of the question.
- For deadlines, fees and eligibility, remind the student to verify on the official page."""

REWRITE_PROMPT = """You turn a student's message into a search query for the website of the
Deggendorf Institute of Technology (DIT / THD).
- Resolve references to earlier conversation turns so the query stands alone.
- Use precise, keyword-rich wording that would appear on the relevant web page
  (e.g. "application deadline summer semester", "address main campus Deggendorf").
- If no campus is specified and one is needed, assume the main campus in Deggendorf.
- Users are students: questions about "applying" mean applying for a study programme
  (e.g. "study programme application deadline summer semester PRIMUSS"), not for a job,
  unless the user explicitly mentions jobs or professorships.
Return only the query, max 20 words."""


class RAG:
    def __init__(self):
        client = chromadb.PersistentClient(path=str(C.CHROMA_DIR))
        self.col = client.get_collection(
            C.COLLECTION, embedding_function=embedding_functions.DefaultEmbeddingFunction())
        self.chunks = [json.loads(l) for l in open(C.CHUNKS_FILE, encoding="utf-8")]
        self.by_id = {c["id"]: c for c in self.chunks}
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in self.chunks])
        # Field boosting: a separate keyword index over page title + URL slug,
        # so pages whose *topic* matches the question rank higher.
        self.bm25_title = BM25Okapi([tokenize(c["title"] + " " + re.sub(r"[/_\-.]", " ", c["url"][22:]))
                                     for c in self.chunks])
        self.llm = None
        if C.LLM_API_KEY:
            from openai import OpenAI
            self.llm = OpenAI(api_key=C.LLM_API_KEY, base_url=C.LLM_BASE_URL)

    # ------------------------------------------------------------ retrieval
    def retrieve(self, query: str | list[str], k: int = C.TOP_K) -> list[dict]:
        """Hybrid retrieval for one or more query variants, merged with Reciprocal Rank Fusion."""
        queries = list(dict.fromkeys([query] if isinstance(query, str) else query))
        n = C.CANDIDATES
        rankings, weights, dense_dist = [], [], {}
        for qi, q in enumerate(queries):
            w = 1.0 if qi == 0 else 0.5           # first query (the rewritten one) counts most
            weights += [w, w, w]
            dense = self.col.query(query_texts=[q], n_results=n)
            rankings.append(dense["ids"][0])
            for cid, d in zip(dense["ids"][0], dense["distances"][0]):
                dense_dist[cid] = min(d, dense_dist.get(cid, 2.0))

            q_tokens = tokenize(q)
            scores = self.bm25.get_scores(q_tokens)
            rankings.append([self.chunks[i]["id"] for i in scores.argsort()[::-1][:n] if scores[i] > 0])
            tscores = self.bm25_title.get_scores(q_tokens)
            rankings.append([self.chunks[i]["id"] for i in tscores.argsort()[::-1][:n] if tscores[i] > 0])

        # Reciprocal Rank Fusion: robust way to merge rankings with different score scales.
        fused: dict[str, float] = {}
        for ranking, w in zip(rankings, weights):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + w / (60 + rank)

        # Intent-aware re-ranking: most users are (prospective) students, so job/professor
        # recruitment pages are down-weighted unless the question is about jobs.
        if not any(JOB_INTENT.search(q) for q in queries):
            for cid in fused:
                c = self.by_id.get(cid)
                if c and JOB_PAGE.search(c["url"] + " " + c["title"]):
                    fused[cid] *= 0.4

        results, per_url = [], {}
        for cid in sorted(fused, key=fused.get, reverse=True):
            c = self.by_id.get(cid)
            if not c or per_url.get(c["url"], 0) >= C.MAX_CHUNKS_PER_URL:
                continue
            per_url[c["url"]] = per_url.get(c["url"], 0) + 1
            # Some site pages are double-encoded ("&amp;"), so decode entities for display/prompt.
            results.append({**c, "text": html.unescape(c["text"]), "title": html.unescape(c["title"]),
                            "score": round(fused[cid], 4),
                            "similarity": round(1 - dense_dist[cid], 3) if cid in dense_dist else None})
            if len(results) >= k:
                break
        return results

    # ------------------------------------------------------------ generation
    def _rewrite(self, question: str, history: list[dict]) -> str:
        if not self.llm:
            return question
        convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:]) or "(none)"
        try:
            r = self.llm.chat.completions.create(
                model=C.LLM_MODEL, temperature=0, max_tokens=400, **self._extra(),
                messages=[{"role": "system", "content": REWRITE_PROMPT},
                          {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {question}"}])
            q = (r.choices[0].message.content or "").strip().strip('"')
            return q or question
        except Exception:
            return question

    @staticmethod
    def _extra() -> dict:
        # gpt-oss models on Groq support a reasoning-effort knob; low keeps latency down.
        return {"extra_body": {"reasoning_effort": "low"}} if "gpt-oss" in C.LLM_MODEL else {}

    def answer(self, question: str, history: list[dict] | None = None) -> dict:
        t0 = time.time()
        history = history or []
        query = self._rewrite(question, history)
        # Search with both the rewritten query and (for first questions) the original wording.
        passages = self.retrieve([query] if history else [query, question])
        sources = [{"n": i + 1, "title": p["title"], "url": p["url"], "type": p["type"],
                    "snippet": p["text"].split("\n", 1)[-1][:280], "similarity": p["similarity"]}
                   for i, p in enumerate(passages)]

        if not self.llm:
            return {"answer": self._extractive(passages), "sources": sources, "mode": "extractive",
                    "query": query, "latency_ms": int((time.time() - t0) * 1000)}

        context = "\n\n".join(f"[{i + 1}] {p['title']} ({p['url']})\n{p['text']}"
                              for i, p in enumerate(passages))
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-6:]:
            messages.append({"role": m["role"], "content": m["content"][:1500]})
        messages.append({"role": "user",
                         "content": f"Context passages:\n{context}\n\nQuestion: {question}"})
        try:
            kwargs = dict(model=C.LLM_MODEL, messages=messages,
                          temperature=C.LLM_TEMPERATURE, max_tokens=1500)
            try:
                r = self.llm.chat.completions.create(**kwargs, **self._extra())
            except Exception:
                r = self.llm.chat.completions.create(**kwargs)   # retry without optional params
            text = (r.choices[0].message.content or "").strip()
            # Normalise model-specific citation styles (e.g. 【3】, [3†L1-L4]) to [3].
            text = re.sub(r"【\s*(\d+)[^】]*】", r"[\1]", text)
            text = re.sub(r"\[(\d+)†[^\]]*\]", r"[\1]", text)
            text = text.replace(" ", " ").replace("‑", "-")
            if not text:
                raise RuntimeError("empty completion")
            mode = "llm"
        except Exception as e:
            text = (f"_The language model is unavailable right now ({type(e).__name__}). "
                    f"Here are the most relevant passages I found:_\n\n" + self._extractive(passages))
            mode = "extractive"
        return {"answer": text, "sources": sources, "mode": mode, "query": query,
                "latency_ms": int((time.time() - t0) * 1000)}

    @staticmethod
    def _extractive(passages: list[dict]) -> str:
        if not passages:
            return "I could not find anything relevant on the THD website for that question."
        lines = ["Here are the most relevant passages I found on the THD website:", ""]
        for i, p in enumerate(passages[:3]):
            body = p["text"].split("\n", 1)[-1].replace("\n", " ")
            lines.append(f"**[{i + 1}] {p['title']}** — {body[:400]}…")
            lines.append("")
        return "\n".join(lines)

    def stats(self) -> dict:
        pages = {c["url"] for c in self.chunks}
        return {"chunks": len(self.chunks), "pages": len(pages),
                "pdfs": len({c["url"] for c in self.chunks if c["type"] == "pdf"}),
                "model": C.LLM_MODEL if self.llm else None, "llm_enabled": bool(self.llm)}
