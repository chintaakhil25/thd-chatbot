const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const historyEl = document.getElementById("history");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

// ---------- chat storage (saved in the browser, like regular chatbots) ----------
// chat = {id, title, updated, messages: [{role, content, sources?}]}
const STORE_KEY = "thd_chats_v1";
let chats = loadChats();
let currentId = null;
let busy = false;

function loadChats() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || []; } catch { return []; }
}
function saveChats() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(chats.slice(0, 50))); } catch { /* storage full/blocked */ }
}
function currentChat() { return chats.find(c => c.id === currentId); }

// ---------- tiny, safe markdown renderer (escape first, then format) ----------
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function inline(s, msgId) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[.,])/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
    .replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) =>
      nums.split(",").map(n => `<a class="cite" href="#src-${msgId}-${n.trim()}">${n.trim()}</a>`).join(""));
}
function renderMarkdown(md, msgId) {
  const lines = escapeHtml(md).split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join("<br>"), msgId)}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      flushPara(); if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(m[1], msgId)}</li>`;
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      flushPara(); if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(m[1], msgId)}</li>`;
    } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
      flushPara(); closeList(); html += `<p><b>${inline(m[1], msgId)}</b></p>`;
    } else if (line.trim() === "") {
      flushPara(); closeList();
    } else if (/^\|?\s*:?-{3,}/.test(line)) {
      // markdown table separator row: skip
    } else {
      closeList(); para.push(line.replace(/^\|\s*|\s*\|$/g, "").replace(/\s*\|\s*/g, " · "));
    }
  }
  flushPara(); closeList();
  return html;
}

// ---------- rendering ----------
function addBubble(role, html) {
  welcomeEl.style.display = "none";
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="avatar">${role === "user" ? "You" : "THD"}</div><div class="bubble">${html}</div>`;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap.querySelector(".bubble");
}

function renderSources(sources, msgId) {
  if (!sources || !sources.length) return "";
  // Group passages from the same page into one card, e.g. "[1, 2]".
  const groups = [];
  for (const s of sources) {
    const g = groups.find(x => x.url === s.url);
    if (g) g.ns.push(s.n); else groups.push({ ...s, ns: [s.n] });
  }
  const items = groups.map(s => `
    ${s.ns.slice(1).map(n => `<span id="src-${msgId}-${n}"></span>`).join("")}
    <a class="source" id="src-${msgId}-${s.n}" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">
      <div class="s-head"><span class="s-num">[${s.ns.join(", ")}]</span>
        <span class="s-title">${escapeHtml(s.title)}</span>${s.type === "pdf" ? '<span class="s-tag">PDF</span>' : ""}</div>
      <div class="s-url">${escapeHtml(s.url)}</div>
    </a>`).join("");
  return `<details class="sources"><summary class="sources-title">Sources (${groups.length})</summary>${items}</details>`;
}

function botHtml(m, msgId) {
  return renderMarkdown(m.content, msgId) + renderSources(m.sources, msgId);
}

function renderHistory() {
  if (!chats.length) { historyEl.innerHTML = '<div class="history-empty">No chats yet</div>'; return; }
  historyEl.innerHTML = "";
  for (const c of chats) {
    const item = document.createElement("div");
    item.className = "history-item" + (c.id === currentId ? " active" : "");
    item.innerHTML = `<span class="h-title" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</span>
                      <button class="h-del" title="Delete chat">✕</button>`;
    item.addEventListener("click", () => openChat(c.id));
    item.querySelector(".h-del").addEventListener("click", e => { e.stopPropagation(); deleteChat(c.id); });
    historyEl.appendChild(item);
  }
}

function clearMessages() {
  messagesEl.querySelectorAll(".msg").forEach(m => m.remove());
}

function openChat(id) {
  if (busy) return;
  currentId = id;
  clearMessages();
  const chat = currentChat();
  welcomeEl.style.display = chat && chat.messages.length ? "none" : "";
  (chat ? chat.messages : []).forEach((m, i) => {
    if (m.role === "user") addBubble("user", escapeHtml(m.content).replace(/\n/g, "<br>"));
    else addBubble("bot", botHtml(m, `${id}-${i}`));
  });
  renderHistory();
  input.focus();
}

function newChat() {
  if (busy) return;
  currentId = null;
  clearMessages();
  welcomeEl.style.display = "";
  renderHistory();
  input.focus();
}

function deleteChat(id) {
  chats = chats.filter(c => c.id !== id);
  saveChats();
  if (id === currentId) newChat(); else renderHistory();
}

// ---------- asking ----------
async function ask(question) {
  question = question.trim();
  if (busy || !question) return;
  busy = true; sendBtn.disabled = true;

  let chat = currentChat();
  if (!chat) {
    chat = { id: String(Date.now()), title: question.slice(0, 60), updated: Date.now(), messages: [] };
    chats.unshift(chat);
    currentId = chat.id;
  }
  const history = chat.messages.slice(-6).map(m => ({ role: m.role, content: m.content }));
  chat.messages.push({ role: "user", content: question });
  saveChats(); renderHistory();

  addBubble("user", escapeHtml(question).replace(/\n/g, "<br>"));
  input.value = ""; autoGrow();
  const bubble = addBubble("bot", '<div class="typing"><span></span><span></span><span></span></div>');

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, history }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const data = await res.json();
    const msg = { role: "assistant", content: data.answer, sources: data.sources };
    chat.messages.push(msg);
    chat.updated = Date.now();
    // Most recently used chat goes to the top of the list.
    chats = [chat, ...chats.filter(c => c.id !== chat.id)];
    saveChats();
    bubble.innerHTML = botHtml(msg, `${chat.id}-${chat.messages.length - 1}`);
  } catch (err) {
    bubble.innerHTML = `<p>⚠️ Sorry, something went wrong: ${escapeHtml(err.message || err)}. Please try again.</p>`;
    chat.messages.pop();                   // don't keep the unanswered question
    if (!chat.messages.length) chats = chats.filter(c => c.id !== chat.id);
    saveChats();
  } finally {
    busy = false; sendBtn.disabled = false; input.focus();
    renderHistory();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }

form.addEventListener("submit", e => { e.preventDefault(); ask(input.value); });
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); }
});
input.addEventListener("input", autoGrow);
document.querySelectorAll(".suggestions button").forEach(b => b.addEventListener("click", () => ask(b.textContent)));
document.getElementById("newChat").addEventListener("click", newChat);
// Clicking a citation [n] opens the (collapsed) source list and highlights that source.
messagesEl.addEventListener("click", e => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  e.preventDefault();
  const target = document.getElementById(cite.getAttribute("href").slice(1));
  if (!target) return;
  const details = target.closest("details");
  if (details) details.open = true;
  const card = target.classList.contains("source") ? target : target.nextElementSibling;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1200);
});

renderHistory();
input.focus();
