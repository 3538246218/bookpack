"use strict";

(() => {
  const dialog = $("radar-dialog");
  const motionDuration = 500;
  const filters = { channel: "male", rank: "read", q: "", category: "", status: "", min_reads: "0", sort: "reads" };
  let items = [], token = 0, loading = false, searchTimer, statusTimer, refreshKey = "", selecting = false;
  let renderedBoard = { channel: filters.channel, rank: filters.rank }, lastRefresh = { running: false };
  let closing = null, resultsAnimation;
  const dateLabel = value => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}` : "未知日期";
  function count(value) {
    const number = Number(value) || 0;
    if (number >= 100000000) return (number / 100000000).toFixed(1).replace(/\.0$/, "") + "亿";
    if (number >= 10000) return (number / 10000).toFixed(1).replace(/\.0$/, "") + "万";
    return number.toLocaleString("zh-CN");
  }
  function error(message = "") {
    $("radar-error").textContent = message;
    $("radar-error").hidden = !message;
  }
  function syncFilters() {
    document.querySelectorAll("[data-radar-channel]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.radarChannel === filters.channel)));
    document.querySelectorAll("[data-radar-rank]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.radarRank === filters.rank)));
    $("radar-query").value = filters.q;
    $("radar-status").value = filters.status;
    $("radar-min-reads").value = filters.min_reads;
    $("radar-sort").value = filters.sort;
  }
  function card(book) {
    const movement = book.new_entry ? '<span class="radar-movement">新上榜</span>' : book.rank_change
      ? `<span class="radar-movement${book.rank_change < 0 ? " is-down" : ""}">${book.rank_change > 0 ? "↑" : "↓"} ${Math.abs(book.rank_change)} 位</span>` : "";
    const reading = Number(book.reads) > 0 ? `<strong>${count(book.reads)}</strong><span>在读</span>` : '<span>在读数据未提供</span>';
    return `<article class="radar-card" data-book-id="${escapeHtml(book.book_id)}">
      <div class="radar-card-top"><span class="radar-position">${escapeHtml(book.category || "未分类")} · 第 ${book.rank_pos} 名</span>${movement}</div>
      <h3>${escapeHtml(book.title)}</h3><p class="radar-author">${escapeHtml(book.author || "作者未提供")}</p>
      <div class="radar-card-bottom"><div><div class="radar-reads">${reading}</div><p class="radar-book-state">${escapeHtml([book.status, book.chapters ? book.chapters + " 章" : ""].filter(Boolean).join(" · "))}</p></div>
      <button class="action-button glass radar-select" type="button" data-radar-select="${escapeHtml(book.book_id)}" aria-label="搜索《${escapeHtml(book.title)}》"><span class="radar-select-label">搜索此书</span></button></div>
    </article>`;
  }
  function finishLoading() {
    loading = false;
    $("radar-books").inert = false;
    $("radar-books").classList.remove("is-loading");
    $("radar-books").setAttribute("aria-busy", "false");
    $("radar-loading").hidden = true;
    $("radar-more").disabled = false;
  }
  async function load(append = false) {
    if (closing) return;
    const request = ++token;
    clearTimeout(searchTimer);
    resultsAnimation?.cancel();
    loading = true;
    error();
    $("radar-reload").hidden = true;
    const hasCards = !!$("radar-books").querySelector(".radar-card:not(.radar-skeleton)");
    $("radar-books").setAttribute("aria-busy", "true");
    $("radar-books").classList.add("is-loading");
    $("radar-books").inert = true;
    $("radar-loading").hidden = false;
    const requestedBoard = (filters.channel === "male" ? "男频" : "女频") + (filters.rank === "read" ? "阅读榜" : "新书榜");
    $("radar-loading-label").textContent = append ? "正在加载更多书籍…" : `正在读取${requestedBoard}${hasCards ? "，当前暂显上次结果…" : "…"}`;
    $("radar-more").disabled = true;
    if (append) $("radar-more").textContent = "正在加载…";
    if (!append) {
      $("radar-empty").hidden = true;
      $("radar-more").hidden = true;
      if (!hasCards) {
        $("radar-books").innerHTML = Array.from({ length: 6 }, () => '<article class="radar-card radar-skeleton" aria-hidden="true"><span class="skeleton-line"></span><span class="skeleton-line is-title"></span><span class="skeleton-line is-author"></span><span class="skeleton-line is-number"></span></article>').join("");
        $("radar-summary").textContent = "正在读取榜单…";
        $("radar-discover").hidden = true;
      }
    }
    try {
      const payload = await api("/api/radar?" + new URLSearchParams({ ...filters, offset: String(append ? items.length : 0), limit: "30" }));
      if (request !== token || !dialog.open) return;
      items = append ? items.concat(payload.items) : payload.items;
      renderedBoard = { channel: payload.channel, rank: payload.rank };
      if (append) $("radar-books").insertAdjacentHTML("beforeend", payload.items.map(card).join(""));
      else {
        $("radar-books").innerHTML = items.map(card).join("");
        $("radar-scroll").scrollTop = 0;
        resultsAnimation = bookFeedback.reveal($("radar-books"), motionDuration);
      }
      const options = ['<option value="">全部分类</option>', ...payload.categories.map(category => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`)].join("");
      if ($("radar-category").innerHTML !== options) $("radar-category").innerHTML = options;
      $("radar-category").value = filters.category;
      const board = (filters.channel === "male" ? "男频" : "女频") + (filters.rank === "read" ? "阅读榜" : "新书榜");
      $("radar-summary").textContent = payload.available
        ? `${board} · 快照 ${dateLabel(payload.date)} · ${payload.total} 条结果${payload.previous_date ? " · 名次对比 " + dateLabel(payload.previous_date) : ""}`
        : `${board} · 暂无已保存的榜单`;
      $("radar-empty").hidden = !!items.length;
      $("radar-empty").textContent = payload.available ? "没有符合筛选条件的书，试试清除筛选。" : "点击“刷新当前榜”获取榜单。";
      $("radar-more").hidden = items.length >= payload.total;
      $("radar-more").textContent = `加载更多（${items.length} / ${payload.total}）`;
      $("radar-discover").hidden = !payload.available;
      $("radar-heat").innerHTML = '<span class="radar-chip-label">分类在读</span>' + payload.heat.map(row => `<button class="radar-chip" type="button" data-radar-category="${escapeHtml(row.category)}" aria-pressed="${filters.category === row.category}" title="上榜作品在读合计 ${count(row.total_reads)}">${escapeHtml(row.category)}<span>${count(row.total_reads)}</span></button>`).join("");
      $("radar-hotwords").innerHTML = payload.hotwords.length ? '<span class="radar-chip-label">书名热词</span>' + payload.hotwords.map(row => `<button class="radar-chip" type="button" data-radar-word="${escapeHtml(row.word)}" aria-pressed="${filters.q === row.word}">${escapeHtml(row.word)}<span>${row.count}</span></button>`).join("") : "";
      if (payload.warnings.length) error("部分快照未能读取，当前显示可用数据。");
      updateStatus(payload.refresh);
    } catch (failure) {
      if (request !== token || !dialog.open) return;
      error(failure.message + (hasCards ? " 仍显示上次读取的结果。" : ""));
      $("radar-reload").hidden = false;
      if (!hasCards) {
        items = [];
        $("radar-books").replaceChildren();
        $("radar-summary").textContent = "榜单读取失败";
        $("radar-empty").hidden = false;
        $("radar-empty").textContent = "点击“重试读取榜单”，可以继续。";
      }
      $("radar-more").hidden = true;
    } finally {
      if (request === token) finishLoading();
    }
  }
  function updateStatus(status) {
    $("radar-refresh").disabled = status.running || selecting;
    $("radar-refresh-label").textContent = status.running ? "正在刷新…" : "刷新当前榜";
    $("radar-refresh-spinner").hidden = !status.running;
    $("radar-progress-wrap").hidden = !status.message;
    $("radar-progress").textContent = status.message || "";
    $("radar-progress").classList.toggle("is-complete", !status.running && !!status.updated_at && !status.error);
    $("radar-refresh-progress").hidden = !status.running;
    const progress = status.total ? Math.round(100 * status.completed / status.total) : null;
    $("radar-refresh-progress").classList.toggle("is-indeterminate", progress === null);
    if (progress === null) $("radar-refresh-progress").removeAttribute("aria-valuenow");
    else $("radar-refresh-progress").setAttribute("aria-valuenow", progress);
    $("radar-refresh-fill").style.width = (progress ?? 0) + "%";
    if (lastRefresh.running && !status.running && status.updated_at && !status.error) bookFeedback.confirm($("radar-refresh"));
    lastRefresh = status;
    if (status.error) error(status.error);
    clearTimeout(statusTimer);
    if (status.running && dialog.open) statusTimer = setTimeout(pollStatus, 1000);
  }
  async function pollStatus() {
    try {
      const status = await api("/api/radar/status");
      if (!dialog.open || closing) return;
      updateStatus(status);
      if (!status.running && status.updated_at) {
        const key = `${status.channel}/${status.rank}/${status.updated_at}/${status.books}`;
        if (key !== refreshKey && !selecting) { refreshKey = key; await load(); }
      }
    } catch (failure) {
      if (dialog.open) { error(failure.message); statusTimer = setTimeout(pollStatus, 3000); }
    }
  }
  function change(values) {
    if (closing) return;
    Object.assign(filters, values);
    syncFilters();
    load();
  }
  function closeRadar(reason = "") {
    if (!dialog.open) return Promise.resolve();
    if (closing) return closing;
    ++token;
    clearTimeout(searchTimer); clearTimeout(statusTimer);
    resultsAnimation?.cancel();
    dialog.inert = true;
    dialog.classList.add("is-closing");
    // Finish the real exit animation; reduced motion has no animation to wait for.
    const animations = dialog.getAnimations().filter(animation => animation.animationName === "radar-leave");
    closing = Promise.allSettled(animations.map(animation => animation.finished)).then(() => {
      if (dialog.open) dialog.close(reason);
      dialog.classList.remove("is-closing");
      dialog.inert = false;
      closing = null;
    });
    return closing;
  }
  $("radar-open").addEventListener("click", () => {
    if (dialog.open || closing) return;
    bookSuggestions.close();
    $("ocean-panel").hidden = true;
    $("ocean-toggle").setAttribute("aria-expanded", "false");
    dialog.returnValue = "";
    dialog.showModal();
    document.body.style.overflow = "hidden";
    syncFilters();
    load();
  });
  $("radar-close").addEventListener("click", () => closeRadar());
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeRadar(); });
  dialog.addEventListener("click", event => { if (event.target === dialog) {
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeRadar();
  } });
  dialog.addEventListener("close", () => {
    ++token;
    clearTimeout(searchTimer); clearTimeout(statusTimer);
    document.body.style.overflow = "";
    finishLoading();
    if (dialog.returnValue !== "search") $("radar-open").focus({ preventScroll: true });
  });
  document.querySelectorAll("[data-radar-channel]").forEach(button => button.addEventListener("click", () => change({ channel: button.dataset.radarChannel, category: "" })));
  document.querySelectorAll("[data-radar-rank]").forEach(button => button.addEventListener("click", () => change({ rank: button.dataset.radarRank, category: "" })));
  $("radar-query").addEventListener("input", event => {
    clearTimeout(searchTimer);
    if (event.isComposing) return;
    filters.q = event.target.value;
    // Invalidate the previous request immediately, before the debounce expires.
    ++token;
    searchTimer = setTimeout(() => load(), 250);
  });
  $("radar-query").addEventListener("compositionend", event => { filters.q = event.target.value; ++token; clearTimeout(searchTimer); searchTimer = setTimeout(() => load(), 250); });
  $("radar-query").addEventListener("compositionstart", () => { ++token; clearTimeout(searchTimer); finishLoading(); });
  for (const [id, key] of [["radar-category", "category"], ["radar-status", "status"], ["radar-min-reads", "min_reads"], ["radar-sort", "sort"]]) {
    $(id).addEventListener("change", event => change({ [key]: event.target.value }));
  }
  $("radar-reset").addEventListener("click", () => change({ q: "", category: "", status: "", min_reads: "0", sort: "reads" }));
  $("radar-discover").addEventListener("click", event => {
    const category = event.target.closest("[data-radar-category]");
    const word = event.target.closest("[data-radar-word]");
    if (category) change({ category: filters.category === category.dataset.radarCategory ? "" : category.dataset.radarCategory });
    else if (word) change({ q: filters.q === word.dataset.radarWord ? "" : word.dataset.radarWord });
  });
  $("radar-more").addEventListener("click", () => { if (!loading) load(true); });
  $("radar-reload").addEventListener("click", () => load());
  $("radar-refresh").addEventListener("click", async () => {
    error();
    $("radar-refresh").disabled = true;
    $("radar-refresh-label").textContent = "正在连接…";
    $("radar-refresh-spinner").hidden = false;
    refreshKey = "";
    try { updateStatus(await api("/api/radar/refresh", { channel: filters.channel, rank: filters.rank })); }
    catch (failure) { error(failure.message); $("radar-refresh").disabled = false; $("radar-refresh-label").textContent = "刷新当前榜"; $("radar-refresh-spinner").hidden = true; }
  });
  $("radar-books").addEventListener("click", async event => {
    const button = event.target.closest("[data-radar-select]");
    if (!button || selecting || loading || closing) return;
    if ($("book-title").disabled) { error("当前获取任务还在进行，完成后可以搜索这本书。"); return; }
    const chosenBoard = { ...renderedBoard }, chosenToken = token;
    const selected = items.find(book => book.book_id === button.dataset.radarSelect);
    selecting = true; button.disabled = true; error();
    button.querySelector(".radar-select-label").textContent = "已选定";
    button.classList.add("is-selected");
    button.closest(".radar-card").classList.add("is-selected");
    $("radar-filters").inert = true;
    $("radar-discover").inert = true;
    $("radar-refresh").disabled = true;
    $("radar-loading").hidden = false;
    $("radar-loading-label").textContent = `已选定《${selected?.title || "这本书"}》，正在带入搜索…`;
    try {
      const book = await api("/api/radar/select", { book_id: button.dataset.radarSelect, ...chosenBoard });
      if (!dialog.open || chosenToken !== token) return;
      if (!bookSuggestions.select(book)) { error("当前获取任务还在进行，完成后可以搜索这本书。"); return; }
      const leaving = closeRadar("search");
      $("search-form").requestSubmit();
      await leaving;
    } catch (failure) { error(failure.message); }
    finally {
      selecting = false; button.disabled = false;
      button.querySelector(".radar-select-label").textContent = "搜索此书";
      button.classList.remove("is-selected");
      button.closest(".radar-card").classList.remove("is-selected");
      $("radar-filters").inert = false;
      $("radar-discover").inert = false;
      $("radar-refresh").disabled = !!lastRefresh.running;
      if (!loading) $("radar-loading").hidden = true;
    }
  });
})();
