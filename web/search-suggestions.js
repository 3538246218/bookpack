"use strict";

const bookSuggestions = (() => {
  const input = document.getElementById("book-title");
  const composer = document.getElementById("search-composer");
  const drawer = document.getElementById("suggestion-drawer");
  const scroll = document.getElementById("suggestion-scroll");
  const list = document.getElementById("suggestion-list");
  const status = document.getElementById("suggestion-status");
  const empty = document.getElementById("suggestion-empty");
  const client = crypto.randomUUID();
  let timer, controller, generation = 0, composing = false, busy = false;
  let rows = [], active = -1, selected = null;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[character]));

  function close() {
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    generation += 1;
    drawer.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }
  function show() {
    if (busy || composing || document.activeElement !== input) return;
    drawer.hidden = false;
    input.setAttribute("aria-expanded", "true");
    // Reserve the bottom control band as well as the on-screen keyboard.
    const viewportBottom = window.visualViewport
      ? window.visualViewport.offsetTop + window.visualViewport.height : innerHeight;
    const viewportTop = window.visualViewport?.offsetTop || 0;
    const position = composer.getBoundingClientRect();
    const launcher = document.getElementById("ocean-toggle").getBoundingClientRect();
    const bottom = Math.min(viewportBottom - 12, launcher.top - 12);
    const belowHeight = Math.max(0, bottom - position.bottom - 9);
    const aboveHeight = Math.max(0, position.top - viewportTop - 21);
    const above = belowHeight < 150 && aboveHeight > belowHeight;
    drawer.dataset.placement = above ? "above" : "below";
    drawer.style.maxHeight = Math.min(330, above ? aboveHeight : belowHeight) + "px";
  }
  function activate(index) {
    active = index;
    [...list.children].forEach((option, offset) => {
      option.setAttribute("aria-selected", String(offset === active));
    });
    if (active >= 0 && list.children[active]) {
      const option = list.children[active];
      input.setAttribute("aria-activedescendant", option.id);
      const top = option.offsetTop, bottom = top + option.offsetHeight;
      if (top < scroll.scrollTop + 10) scroll.scrollTop = top - 10;
      else if (bottom > scroll.scrollTop + scroll.clientHeight - 10) scroll.scrollTop = bottom - scroll.clientHeight + 10;
    } else input.removeAttribute("aria-activedescendant");
  }
  function render(payload, pending = false) {
    const previous = rows[active];
    rows = payload.suggestions || [];
    list.innerHTML = rows.map((row, index) => {
      const metadata = [row.author, row.platform_category].filter(Boolean).map(escape).join(" · ");
      const platform = `<span class="work-platform${row.platform_verified ? "" : " is-unknown"}" title="作品平台">${escape(row.platform_label || "平台未核实")}</span>`;
      return `<li class="suggestion-option" id="title-suggestion-${index}" role="option" aria-selected="false" data-index="${index}">
        <span class="suggestion-copy"><strong>${escape(row.title)}</strong>${metadata ? `<span>${metadata}</span>` : ""}</span>${platform}</li>`;
    }).join("");
    activate(previous ? rows.findIndex(row => row.title === previous.title && row.author === previous.author) : -1);
    status.textContent = pending ? "继续查找…" : "";
    empty.hidden = !!rows.length;
    empty.textContent = pending ? "正在匹配书名…" : payload.online_unavailable
      ? "在线书源暂未响应，可直接搜索原书名。" : "暂无建议，可直接搜索原书名。";
    show();
  }
  async function request(query, online, token, signal) {
    const response = await fetch("/api/suggest", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ query, online, client_id: client, generation: token })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "书名建议暂不可用");
    return payload;
  }
  async function load(query, token) {
    if (token !== generation || busy || composing) return;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const local = await request(query, false, token, signal);
      if (token !== generation || signal.aborted) return;
      render(local, local.online_pending);
      if (local.online_pending) {
        const remote = await request(query, true, token, signal);
        if (token !== generation || signal.aborted || remote.cancelled) return;
        render(remote);
      }
    } catch (error) {
      if (token !== generation || error.name === "AbortError") return;
      if (rows.length) { status.textContent = "在线建议暂不可用"; return; }
      render({ suggestions: [], online_unavailable: true });
    }
  }
  function schedule() {
    close();
    rows = []; list.replaceChildren();
    const query = input.value.trim();
    if (busy || composing || query.split(/[|\t]/)[0].replace(/[^\p{L}\p{N}]/gu, "").length < 2) return;
    const token = generation;
    timer = setTimeout(() => load(query, token), 350);
  }
  function choose(index) {
    const row = rows[index];
    select(row);
  }
  function select(row) {
    if (!row || busy) return false;
    selected = row;
    input.value = row.title;
    close();
    input.focus({ preventScroll: true });
    input.dispatchEvent(new CustomEvent("suggestion-picked", { bubbles: true }));
    return true;
  }
  input.addEventListener("input", event => {
    selected = null;
    if (event.isComposing || composing) { close(); return; }
    schedule();
  });
  input.addEventListener("compositionstart", () => { composing = true; selected = null; close(); });
  input.addEventListener("compositionend", () => { composing = false; schedule(); });
  input.addEventListener("focus", schedule);
  input.addEventListener("blur", () => { if (!composer.contains(document.activeElement)) close(); });
  input.addEventListener("keydown", event => {
    if (event.isComposing || composing || event.keyCode === 229) return;
    if (event.key === "Escape") { close(); return; }
    if (event.key === "Tab") { close(); return; }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && rows.length) {
      event.preventDefault(); show();
      activate(event.key === "ArrowDown" ? (active + 1) % rows.length : (active < 0 ? rows.length - 1 : (active - 1 + rows.length) % rows.length));
    } else if (event.key === "Enter" && !drawer.hidden && active >= 0) {
      event.preventDefault(); choose(active);
    }
  });
  list.addEventListener("pointerdown", event => event.preventDefault());
  list.addEventListener("click", event => {
    const option = event.target.closest("[data-index]");
    if (option) choose(Number(option.dataset.index));
  });
  document.addEventListener("pointerdown", event => { if (!composer.contains(event.target)) close(); });
  const reposition = () => { if (!drawer.hidden) show(); };
  window.addEventListener("resize", reposition);
  window.addEventListener("scroll", reposition, { passive: true });
  window.visualViewport?.addEventListener("resize", reposition);
  window.visualViewport?.addEventListener("scroll", reposition);
  return {
    close,
    select,
    isComposing() { return composing; },
    searchText(title) {
      return selected?.title === title && selected.author ? title + " | " + selected.author : title;
    },
    setBusy(value) { if (value && !busy) close(); busy = value; }
  };
})();
