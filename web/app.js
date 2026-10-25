"use strict";

const $ = (id) => document.getElementById(id);
const activeStates = new Set(["running", "stopping", "packing"]);
const state = {
  batches: [],
  current: new URLSearchParams(location.search).get("batch") || sessionStorage.getItem("current-book") || null,
  starting: false,
  action: false,
  refreshing: false,
  initialized: false,
  signature: "",
  timer: null,
  pending: null,
  completionTarget: null,
  actionPath: ""
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}
function linkUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? escapeHtml(url.href) : "";
  } catch { return ""; }
}
function platformMarkup(work) {
  return `<span class="work-platform${work.platform_verified ? "" : " is-unknown"}" title="作品平台">${escapeHtml(work.platform_label || "平台未核实")}</span>`;
}
async function api(url, data) {
  let response;
  try {
    response = await fetch(url, data === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
    });
  } catch {
    throw new Error("本地服务暂时未连接，请重新启动软件。");
  }
  let payload;
  try { payload = await response.json(); }
  catch { throw new Error("服务响应异常，请稍后重试。"); }
  if (!response.ok) throw new Error(payload.error || "操作未完成，请稍后重试。");
  return payload;
}
function formError(message = "") {
  $("form-error").textContent = message;
  $("form-error").hidden = !message;
  $("book-title").setAttribute("aria-invalid", String(!!message));
}
function feedback(message = "") {
  $("feedback").textContent = message;
  $("feedback").hidden = !message;
}
function setCurrent(id) {
  state.current = id || null;
  if (id) sessionStorage.setItem("current-book", id);
  else sessionStorage.removeItem("current-book");
  const address = new URL(location.href);
  if (address.searchParams.has("batch")) {
    if (id) address.searchParams.set("batch", id);
    else address.searchParams.delete("batch");
    history.replaceState(null, "", address.pathname + address.search + address.hash);
  }
  state.signature = "";
}
function currentBatch() { return state.batches.find(batch => batch.id === state.current); }
function busyBatch() { return state.batches.find(batch => activeStates.has(batch.status)); }

function taskCaption(task) {
  if (task.pending_candidate) return "正在切换书籍";
  if (task.status === "done" && task.content_issues?.length) return "文件存在正文问题";
  if (task.status === "done") return "已获取";
  if (task.status === "needs_choice") return "请选择书籍";
  if (task.status === "failed") return "未能获取";
  if (task.status === "cancelled") return "已停止";
  if (task.status === "checking") return "正在整理文件";
  if (task.status === "searching") return "正在匹配书名与书源";
  if (task.chapter_count) return `已获取 ${task.chapter_count}${task.expected_chapters ? " / " + task.expected_chapters : ""} 章`;
  if (task.page_count) return `已汇集 ${task.page_count} 页`;
  return task.status === "downloading" ? "正在获取全文" : "正在准备";
}
function batchCaption(batch) {
  if (batch.status === "running" && batch.tasks.some(task => task.pending_candidate)) return "正在切换书籍";
  if (!activeStates.has(batch.status) && batch.tasks.some(task => task.content_issues?.length)) return "已生成文件，存在缺损待修复";
  if (batch.status === "partial" && !batch.tasks.some(task => task.status === "done")) return "暂未获取成功";
  const labels = {
    stopping: "正在停止", packing: "正在打包", complete: "已打包，可以下载",
    needs_choice: "请选择要获取的书籍",
    partial: "部分已完成", cancelled: "已停止", interrupted: "上次获取已中断", error: "暂未获取成功"
  };
  if (batch.status !== "running") return labels[batch.status] || "等待处理";
  const task = batch.tasks.find(item => !["done", "failed", "cancelled"].includes(item.status));
  return task?.status === "searching" ? "正在找书" : "正在获取";
}
function candidateListMarkup(task, switching) {
  return `<ul class="candidate-list" aria-label="候选书籍">${(task.candidates || []).map(candidate => {
    const name = escapeHtml(candidate.matched_title);
    const details = [candidate.matched_author || "作者待核实", candidate.platform_category].filter(Boolean).map(escapeHtml).join(" · ");
    const current = switching && candidate.candidate_id === task.selected_candidate_id;
    const reading = candidate.download_kind === "online_only";
    const action = current ? "当前作品" : reading ? "前往阅读 ↗" : switching ? "改选 →" : "获取 →";
    const content = `<span class="candidate-copy"><strong>${name}</strong><span>${details}</span>${platformMarkup(candidate)}</span>
      <span class="candidate-action"><span>${candidate.match_kind === "prefix" ? "书名前缀" : `相似度 ${Math.round(candidate.match_score * 100)}%`}</span><span>${action}</span></span>`;
    return `<li>${reading
      ? `<a class="candidate-option glass" href="${linkUrl(candidate.source_page)}" target="_blank" rel="noopener noreferrer">${content}</a>`
      : `<button class="candidate-option glass${current ? " is-current" : ""}" type="button" data-task-id="${escapeHtml(task.id)}" data-candidate-id="${escapeHtml(candidate.candidate_id)}" data-current="${current}" ${current ? 'disabled aria-current="true"' : ""} aria-label="${current ? "当前作品" : switching ? "改选" : "获取"}《${name}》">${content}</button>`}</li>`;
  }).join("")}</ul>`;
}
function taskMarkup(task) {
  const author = task.matched_author || task.author || "";
  const metadata = [
    author ? escapeHtml(author) : "",
    task.platform_category ? escapeHtml(task.platform_category) : "",
    task.status === "needs_choice" ? "" : escapeHtml(taskCaption(task)),
    task.match_kind === "fuzzy" && Number.isFinite(task.match_score)
      ? `相近书名 · 相似度 ${Math.round(task.match_score * 100)}%` : ""
  ].filter(Boolean).join(" · ");
  const matches = (task.candidates?.length ? [] : task.source_matches || []).map(match => {
    const url = linkUrl(match.source_page);
    return url ? `<a class="read-link glass" href="${url}" target="_blank" rel="noopener noreferrer">${match.platform_verified ? "前往" + escapeHtml(match.platform_label) + "阅读" : "前往阅读"} ↗</a>` : "";
  }).join("");
  const failed = task.status === "failed";
  const error = failed && task.error ? `<p class="task-error">这次没有获取成功，可以重试或补充作者后再搜。</p>
    <details class="error-details" data-details-key="${escapeHtml(task.id)}:error"><summary class="glass">查看原因</summary><p class="error-detail-copy">${escapeHtml(task.error)}</p></details>` : "";
  const issues = task.content_issues?.length && task.status === "done"
    ? `<p class="task-error">已确认正文存在问题，可点击“修复正文”重新获取。</p><details class="error-details" data-details-key="${escapeHtml(task.id)}:quality"><summary class="glass">查看问题章节</summary><p class="error-detail-copy">${task.content_issues.map(row => escapeHtml(row.title + "：" + row.message)).join("<br>")}</p></details>` : "";
  const candidates = task.status === "needs_choice" ? `<p class="candidate-hint">${escapeHtml(task.error || "找到相近书名，点击即可获取。")}</p>
    ${candidateListMarkup(task, false)}` : task.candidates?.length
      ? `<details class="alternative-details" data-details-key="${escapeHtml(task.id)}:alternatives"><summary class="glass">其他候选<svg class="alternative-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg></summary>${candidateListMarkup(task, true)}</details>` : "";
  return `<article class="book-item" data-task-id="${escapeHtml(task.id)}" data-status="${escapeHtml(task.status)}">
    <div class="book-heading"><h2 class="book-name">${escapeHtml(task.matched_title || task.title)}</h2>${platformMarkup(task)}</div>
    ${metadata ? `<p class="book-meta">${metadata}</p>` : ""}${issues}${candidates}${error}${matches}
  </article>`;
}
function progressValue(batch) {
  let known = false;
  let total = 0;
  for (const task of batch.tasks) {
    if (["done", "failed", "cancelled", "needs_choice"].includes(task.status)) { total += 1; known = true; }
    else if (task.total) { total += Math.min(.98, task.bytes / task.total); known = true; }
    else if (task.expected_chapters) { total += Math.min(.98, task.chapter_count / task.expected_chapters); known = true; }
  }
  return known ? Math.round(100 * total / Math.max(1, batch.tasks.length)) : null;
}
function renderStages(index, failed = false) {
  $("task-stages").hidden = false;
  [...$("task-stages").children].forEach((step, position) => {
    step.classList.toggle("is-done", position < index || index === 3);
    step.classList.toggle("is-current", position === index);
    step.classList.toggle("is-failed", position === index && failed);
    if (position === index) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
  });
}
function renderPending() {
  const pending = state.pending;
  const failed = !!pending.error;
  $("result").dataset.batchId = "";
  $("result").dataset.phase = failed ? "error" : "finding";
  $("result-status").textContent = failed ? "未能开始查找，书名已保留" : `正在查找《${pending.title}》`;
  $("state-dot").classList.toggle("is-active", !failed);
  $("state-dot").classList.toggle("is-error", failed);
  $("state-dot").classList.remove("is-success");
  $("book-list").innerHTML = `<article class="book-item"><div class="book-heading"><h2 class="book-name">${escapeHtml(pending.title)}</h2></div>
    ${pending.author ? `<p class="book-meta">${escapeHtml(pending.author)}</p>` : ""}
    ${failed ? `<p class="task-error" role="alert">${escapeHtml(pending.error)}</p><button class="action-button glass" type="button" data-search-again>重新搜索</button>`
      : '<p class="pending-copy">正在匹配书名与可用书源…</p>'}</article>`;
  state.signature = "";
  renderStages(0, failed);
  $("progress").hidden = failed;
  $("progress").classList.add("is-indeterminate");
  $("progress").removeAttribute("aria-valuenow");
  $("progress-fill").style.width = "0%";
  $("dismiss").hidden = !failed;
  ["archive-error", "download", "open-folder", "retry", "cancel"].forEach(id => { $(id).hidden = true; });
}
function render() {
  const batch = currentBatch();
  const busy = busyBatch();
  const active = batch && activeStates.has(batch.status);
  const pending = state.pending && (state.pending.error || state.starting && (!state.pending.batchId || batch?.id !== state.pending.batchId));
  $("search-button").disabled = state.starting || state.action || !!busy;
  $("book-title").disabled = state.starting || state.action || !!busy;
  bookSuggestions.setBusy(state.starting || state.action || !!busy);
  $("search-spinner").hidden = !(state.starting || state.action || busy);
  $("search-button").classList.toggle("is-busy", !!(state.starting || state.action || busy));
  $("search-label").textContent = state.starting ? "搜索中" : busy
    ? busy.status === "packing" ? "打包中" : busy.tasks.some(task => task.status === "searching") ? "搜索中" : "获取中"
    : "搜索";
  document.body.classList.toggle("has-result", !!(batch || pending));
  $("result").hidden = !(batch || pending);
  if (pending) { renderPending(); return; }
  if (!batch) return;

  $("result").dataset.batchId = batch.id;
  $("result-status").textContent = batchCaption(batch);
  $("state-dot").classList.toggle("is-active", !!active);
  const hasIssues = batch.tasks.some(task => task.content_issues?.length);
  const failed = !active && (hasIssues || ["partial", "error", "interrupted"].includes(batch.status));
  const complete = batch.status === "complete" && !!batch.archive && !hasIssues;
  $("state-dot").classList.toggle("is-error", failed);
  $("state-dot").classList.toggle("is-success", complete);
  const stage = complete ? 3 : batch.status === "packing" || batch.archive_error ? 2
    : batch.tasks.some(task => ["downloading", "checking", "done"].includes(task.status)) ? 1 : 0;
  $("result").dataset.phase = complete ? "complete" : failed ? "error" : ["finding", "getting", "packing"][stage];
  renderStages(stage, failed);
  if (complete && state.completionTarget === batch.id && !state.action) {
    state.completionTarget = null;
    bookFeedback.confirm($("result"));
  }
  $("dismiss").hidden = !!active;
  const signature = JSON.stringify(batch.tasks);
  let restoreFocus;
  if (signature !== state.signature) {
    const openDetails = new Set([...$("book-list").querySelectorAll("details[open]")].map(item => item.dataset.detailsKey));
    const scrollTop = $("book-list").scrollTop;
    const focused = document.activeElement;
    const candidateId = focused?.dataset.candidateId, taskId = focused?.dataset.taskId;
    const detailsKey = focused?.matches("summary") ? focused.parentElement.dataset.detailsKey : null;
    $("book-list").innerHTML = batch.tasks.map(taskMarkup).join("");
    $("book-list").querySelectorAll("details").forEach(item => { item.open = openDetails.has(item.dataset.detailsKey); });
    $("book-list").scrollTop = scrollTop;
    restoreFocus = candidateId ? $("book-list").querySelector(`button[data-task-id="${CSS.escape(taskId)}"][data-candidate-id="${CSS.escape(candidateId)}"]`)
      : detailsKey ? $("book-list").querySelector(`[data-details-key="${CSS.escape(detailsKey)}"] > summary`) : null;
    state.signature = signature;
  }
  $("book-list").querySelectorAll("button[data-candidate-id]").forEach(button => {
    const task = batch.tasks.find(item => item.id === button.dataset.taskId);
    button.disabled = button.dataset.current === "true" || !!task?.pending_candidate || state.action || state.starting
      || !!busy && (busy.id !== batch.id || busy.status !== "running");
  });
  restoreFocus?.focus({ preventScroll: true });

  const progress = progressValue(batch);
  $("progress").hidden = !active;
  $("progress").classList.toggle("is-indeterminate", progress === null);
  if (progress === null) $("progress").removeAttribute("aria-valuenow");
  else $("progress").setAttribute("aria-valuenow", progress);
  $("progress-fill").style.width = (progress ?? 0) + "%";
  $("archive-error").hidden = !batch.archive_error;
  $("archive-error").textContent = batch.archive_error || "";
  $("download").hidden = active || !batch.archive;
  $("download").lastChild.textContent = hasIssues ? "下载现有文件" : "下载书包";
  if (batch.archive && !active) $("download").href = "/api/archive/" + encodeURIComponent(batch.id);
  else $("download").removeAttribute("href");
  const hasBooks = batch.tasks.some(task => task.status === "done");
  $("open-folder").hidden = !hasBooks || !!active;
  $("retry").hidden = !!active || (batch.status === "complete" && !!batch.archive && !hasIssues)
    || (batch.status === "needs_choice" && !batch.tasks.some(task => ["failed", "cancelled"].includes(task.status)));
  $("retry").textContent = state.action && state.actionPath === "/api/retry" ? "正在重试…" : hasIssues ? "修复正文" : batch.archive_error && hasBooks ? "重新打包" : "重新获取";
  $("retry").disabled = !!busy || state.action;
  $("cancel").hidden = !active || batch.status === "packing";
  $("cancel").disabled = state.action || batch.status === "stopping";
  $("cancel").textContent = batch.status === "stopping" || state.action && state.actionPath === "/api/cancel" ? "正在停止" : "停止";
  $("open-folder-label").textContent = state.action && state.actionPath === "/api/open-folder" ? "正在打开…" : "打开文件夹";
}

async function refresh() {
  if (state.refreshing) return;
  clearTimeout(state.timer);
  state.refreshing = true;
  let retry = false;
  try {
    const payload = await api("/api/state");
    state.batches = payload.batches;
    if (!state.initialized) {
      const resumed = currentBatch() || busyBatch();
      setCurrent(resumed?.id);
      if (resumed && !$("book-title").value) $("book-title").value = resumed.tasks[0]?.title || "";
      state.initialized = true;
    } else if (!currentBatch() && busyBatch()) {
      setCurrent(busyBatch().id);
    }
    if ($("form-error").dataset.connection) {
      delete $("form-error").dataset.connection;
      formError();
    }
    render();
  } catch (error) {
    $("form-error").dataset.connection = "true";
    formError(error.message);
    retry = true;
  } finally {
    state.refreshing = false;
    if (busyBatch() || retry || state.starting) state.timer = setTimeout(refresh, retry ? 4000 : 900);
  }
}

$("search-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (bookSuggestions.isComposing() || state.starting || busyBatch()) return;
  const title = $("book-title").value.trim();
  if (!title) {
    formError("先输入一个书名。");
    $("book-title").focus();
    return;
  }
  state.starting = true;
  const searchText = bookSuggestions.searchText(title);
  const [pendingTitle, pendingAuthor] = searchText.split("|").map(part => part.trim());
  state.pending = { title: pendingTitle || title, author: pendingAuthor || "", batchId: null };
  bookSuggestions.close();
  formError(); feedback(); render();
  try {
    const payload = await api("/api/start", { text: searchText, mode: "titles", name: title, concurrency: 3 });
    state.pending.batchId = payload.id;
    state.completionTarget = payload.id;
    setCurrent(payload.id);
    // A request already in flight may predate this search; fetch once more afterward.
    while (state.refreshing) await new Promise(resolve => setTimeout(resolve, 40));
    await refresh();
  } catch (error) { state.pending.error = error.message; }
  finally { state.starting = false; if (!state.pending?.error) state.pending = null; render(); }
});
$("book-title").addEventListener("input", () => { formError(); feedback(); });
$("book-title").addEventListener("suggestion-picked", () => { formError(); feedback(); });
$("dismiss").addEventListener("click", () => {
  if (currentBatch() && activeStates.has(currentBatch().status)) return;
  state.pending = null;
  setCurrent(null); feedback(); formError(); render();
  $("book-title").focus();
});
async function act(path, extra = {}) {
  if (state.action || !state.current) return;
  state.action = true; state.actionPath = path; feedback(); render();
  try {
    await api(path, { id: state.current, ...extra });
    if (["/api/retry", "/api/choose"].includes(path)) state.completionTarget = state.current;
    if (path === "/api/open-folder") feedback("文件夹已打开。");
    else {
      while (state.refreshing) await new Promise(resolve => setTimeout(resolve, 40));
      await refresh();
    }
  } catch (error) { feedback(error.message); }
  finally { state.action = false; state.actionPath = ""; render(); }
}
$("cancel").addEventListener("click", () => act("/api/cancel"));
$("retry").addEventListener("click", () => act("/api/retry"));
$("open-folder").addEventListener("click", () => act("/api/open-folder"));
$("book-list").addEventListener("click", event => {
  if (event.target.closest("[data-search-again]")) { $("search-form").requestSubmit(); return; }
  const button = event.target.closest("button[data-candidate-id]");
  if (!button || button.disabled) return;
  act("/api/choose", { task_id: button.dataset.taskId, candidate_id: button.dataset.candidateId });
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
window.addEventListener("focus", refresh);
refresh();
