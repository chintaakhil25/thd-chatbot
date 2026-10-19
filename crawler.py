"""
Step 1 of the pipeline: crawl the public THD website.

- Breadth-first crawl starting from the English homepage, restricted to THD hosts.
- Respects robots.txt, uses a polite delay and bounded retries with backoff.
- Detects page language from <html lang> (fallback: langdetect on the text),
  because many English pages live on short aliases without an /en/ prefix.
- Extracts the main text of HTML pages and selectable text of linked PDFs.
- Persists crawl state in SQLite and extracted records in JSONL. Records keep their
  outgoing links, so an interrupted crawl can resume and still expand the frontier.

Usage:  python crawler.py            (resume / continue)
        python crawler.py --fresh    (delete previous crawl data first)
"""
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup
from langdetect import DetectorFactory, detect
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config as C

DetectorFactory.seed = 0
_lock = threading.Lock()
_local = threading.local()


# ---------------------------------------------------------------- helpers
def session() -> requests.Session:
    """One HTTP session per thread, with bounded retries + exponential backoff."""
    if not hasattr(_local, "s"):
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "HEAD"))
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
        s.headers["User-Agent"] = C.USER_AGENT
        _local.s = s
    return _local.s


def normalize(url: str) -> str | None:
    """Canonical form: https, canonical host, no query/fragment, no trailing slash."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc.lower() not in C.ALLOWED_HOSTS:
        return None
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    low = path.lower()
    if low.endswith(C.SKIP_EXTENSIONS) or any(x in low for x in C.SKIP_PATH_PARTS):
        return None
    return urlunparse(("https", C.CANONICAL_HOST, path, "", "", ""))


def detect_lang(text: str, declared: str | None = None) -> str:
    if declared:
        return declared.split("-")[0].lower()
    try:
        return detect(text[:3000]) if len(text) > 50 else "unknown"
    except Exception:
        return "unknown"


def clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()


# ---------------------------------------------------------------- state
class State:
    def __init__(self):
        C.DATA_DIR.mkdir(exist_ok=True)
        C.PDF_DIR.mkdir(exist_ok=True)
        self.db = sqlite3.connect(C.CRAWL_DB, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS urls(
            url TEXT PRIMARY KEY, kind TEXT, status TEXT, http_code INTEGER,
            lang TEXT, error TEXT, updated TEXT)""")
        self.db.commit()

    def done(self) -> dict:
        return {u: (k, s) for u, k, s in self.db.execute("SELECT url, kind, status FROM urls")}

    def mark(self, url, kind, status, code=None, lang=None, error=None):
        with _lock:
            self.db.execute("INSERT OR REPLACE INTO urls VALUES (?,?,?,?,?,?,?)",
                            (url, kind, status, code, lang, error,
                             datetime.now(timezone.utc).isoformat()))
            self.db.commit()

    def save_record(self, rec: dict):
        with _lock, open(C.RAW_PAGES, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- fetchers
def fetch_html(url: str):
    time.sleep(C.REQUEST_DELAY)
    r = session().get(url, timeout=C.REQUEST_TIMEOUT)
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200:
        return r.status_code, None, ctype, r.url
    return r.status_code, r.text, ctype, r.url


def parse_html(url: str, html: str):
    soup = BeautifulSoup(html, "lxml")
    declared = (soup.html.get("lang") if soup.html else None) or None
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    title = re.sub(r"\s*[|\-–]\s*(DIT|THD|Technische Hochschule Deggendorf).*$", "", title).strip() or title

    links, pdfs = set(), set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(url, href)
        if urlparse(absu).path.lower().endswith(".pdf"):
            p = urlparse(absu)
            if p.netloc.lower() in C.ALLOWED_HOSTS:
                pdfs.add(urlunparse(("https", C.CANONICAL_HOST, p.path, "", "", "")))
            continue
        n = normalize(absu)
        if n:
            links.add(n)

    main = soup.find("main") or soup.body or soup
    for tag in main.find_all(["script", "style", "noscript", "nav", "header", "footer",
                              "form", "iframe", "svg", "button", "aside"]):
        tag.decompose()
    for tag in main.find_all(attrs={"class": re.compile(r"breadcrumb|cookie|share|social|skip|sr-only", re.I)}):
        tag.decompose()
    # Newlines only after block-level elements, so inline links stay inside their sentence.
    for br in main.find_all("br"):
        br.replace_with("\n")
    for blk in main.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
                              "section", "article", "dt", "dd", "table", "ul", "ol"]):
        blk.append("\n")
    for cell in main.find_all(["td", "th"]):
        cell.append(" | ")
    text = clean_text(main.get_text(""))
    return title, text, detect_lang(text, declared), links, pdfs


def process_pdf(url: str, state: State):
    try:
        time.sleep(C.REQUEST_DELAY)
        r = session().get(url, timeout=C.REQUEST_TIMEOUT * 2, stream=True)
        if r.status_code != 200:
            state.mark(url, "pdf", "http_error", r.status_code)
            return
        size = int(r.headers.get("Content-Length") or 0)
        if size > C.MAX_PDF_MB * 1024 * 1024:
            state.mark(url, "pdf", "too_large", 200)
            return
        data = r.content
        name = hashlib.md5(url.encode()).hexdigest()[:12] + ".pdf"
        (C.PDF_DIR / name).write_bytes(data)
        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = [p.get_text() for p in doc]
            title = (doc.metadata or {}).get("title") or urlparse(url).path.split("/")[-1]
        text = clean_text("\n\n".join(pages))
        if len(text) < 200:
            state.mark(url, "pdf", "no_text", 200)   # likely scanned -> would need OCR
            return
        lang = detect_lang(text)
        if lang not in C.ALLOWED_LANGS:
            state.mark(url, "pdf", "skipped_lang", 200, lang)
            return
        state.save_record({"url": url, "type": "pdf", "title": title, "lang": lang,
                           "text": text, "pages": len(pages), "links": []})
        state.mark(url, "pdf", "ok", 200, lang)
    except Exception as e:
        state.mark(url, "pdf", "error", None, None, str(e)[:300])


def pdf_priority(url: str) -> tuple:
    """Rank PDFs so likely-English, student-relevant documents are processed first."""
    name = urlparse(url).path.lower()
    score = 0
    if re.search(r"(^|[_\-/.])(en|eng|english)([_\-.]|$)", name):
        score += 3
    if re.search(r"guide|handbook|faq|international|welcome|checklist|brochure|prospectus|"
                 r"regulation|module|fees|housing|visa|insurance|admission|application", name):
        score += 2
    if re.search(r"(^|[_\-/.])(de|ger|deutsch)([_\-.]|$)|ordnung|satzung|merkblatt|antrag|"
                 r"bekanntmachung|formular|richtlinie", name):
        score -= 3
    return (-score, name)


# ---------------------------------------------------------------- crawl
def crawl(fresh: bool = False):
    if fresh:
        for p in (C.RAW_PAGES, C.CRAWL_DB):
            p.unlink(missing_ok=True)
    state = State()

    rp = robotparser.RobotFileParser()
    try:
        rp.parse(session().get("https://www.th-deg.de/robots.txt", timeout=10).text.splitlines())
    except Exception:
        rp = None

    done = state.done()
    pdf_queue: set[str] = set()
    frontier: list[str] = [u for u in (normalize(x) for x in C.START_URLS) if u]
    # Resume: re-seed the frontier from links stored in already-extracted records.
    if C.RAW_PAGES.exists():
        with open(C.RAW_PAGES, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                frontier.extend(rec.get("links", []))
                pdf_queue.update(rec.get("pdfs", []))
    seen = set(done)
    html_ok = sum(1 for k, s in done.values() if k == "html" and s == "ok")
    frontier = list(dict.fromkeys(u for u in frontier if u not in seen))
    seen.update(frontier)
    print(f"Resuming with {html_ok} pages done, frontier={len(frontier)}")

    def work(url):
        if rp and not rp.can_fetch(C.USER_AGENT, url):
            state.mark(url, "html", "robots_blocked")
            return set(), set()
        try:
            code, html, ctype, final_url = fetch_html(url)
            if html is None or "html" not in ctype:
                state.mark(url, "html", "http_error" if html is None else "not_html", code)
                return set(), set()
            if normalize(final_url) is None:          # redirected off-site
                state.mark(url, "html", "offsite_redirect", code)
                return set(), set()
            title, text, lang, links, pdfs = parse_html(url, html)
            if lang not in C.ALLOWED_LANGS:
                # Out-of-scope language: don't index or expand (keeps crawl English-focused).
                state.mark(url, "html", "skipped_lang", code, lang)
                return set(), set()
            if len(text) > 150:
                state.save_record({"url": url, "type": "html", "title": title, "lang": lang,
                                   "text": text, "links": sorted(links), "pdfs": sorted(pdfs)})
            state.mark(url, "html", "ok", code, lang)
            return links, pdfs
        except Exception as e:
            state.mark(url, "html", "error", None, None, str(e)[:300])
            return set(), set()

    t0 = time.time()
    with ThreadPoolExecutor(C.WORKERS) as pool:
        while frontier and html_ok < C.MAX_HTML_PAGES:
            batch = frontier[: min(len(frontier), C.MAX_HTML_PAGES - html_ok, 60)]
            frontier = frontier[len(batch):]
            for links, pdfs in pool.map(work, batch):
                pdf_queue.update(pdfs)
                for l in links:
                    if l not in seen:
                        seen.add(l)
                        frontier.append(l)
            html_ok = sum(1 for k, s in state.done().values() if k == "html" and s == "ok")
            print(f"  html ok={html_ok}  frontier={len(frontier)}  pdfs found={len(pdf_queue)}  "
                  f"({time.time() - t0:.0f}s)", flush=True)

        pdfs_todo = sorted((u for u in pdf_queue if u not in done), key=pdf_priority)[: C.MAX_PDFS]
        print(f"Processing {len(pdfs_todo)} PDFs ...", flush=True)
        list(pool.map(lambda u: process_pdf(u, state), pdfs_todo))

    summary = {}
    for k, s in state.done().values():
        summary[f"{k}:{s}"] = summary.get(f"{k}:{s}", 0) + 1
    print("Crawl summary:", json.dumps(summary, indent=2))


if __name__ == "__main__":
    crawl(fresh="--fresh" in sys.argv)
