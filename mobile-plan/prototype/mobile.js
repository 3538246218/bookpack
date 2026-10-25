"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const data = window.MOBILE_PREVIEW_DATA;
  const storageKey = "bookpack-mobile-prototype-v1";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const defaults = { preset:"afternoon", waves:56, daylight:55, film:22, motion:true, auto:false };
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { /* A fresh preview remains usable without storage. */ }
  const state = { tab:"search", detailId:"", returnTab:"search", channel:"male", rank:"read", q:"", category:"", status:"", minReads:0, sort:"reads", limit:20,
    settings:{...defaults,...saved.settings}, tasks:Array.isArray(saved.tasks) ? saved.tasks : [], bagFilter:"all", connected:true, scrolls:{}, toastTimer:null };
  const unique = new Map();
  Object.values(data.boards).forEach(board => board.items.forEach(book => unique.set(book.book_id, book)));
  const allBooks = [...unique.values()];
  let searchTimer, composing = false, sheetClosing = null, resultsAnimation, detailSignature = "";
  const normalize = value => String(value).normalize("NFKC").toLocaleLowerCase().replace(/[\s《》,，。:：!！?？·|]/g, "");
  const board = () => data.boards[`${state.channel}/${state.rank}`];
  const active = () => state.tasks.find(task => !["complete","cancelled","error"].includes(task.phase));
  const findBook = id => unique.get(id);
  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify({settings:state.settings,tasks:state.tasks.slice(0,30)})); } catch { /* Storage may be disabled in a browser preview. */ }
  }
  function count(value) {
    return value >= 10000 ? (value / 10000).toFixed(1).replace(/\.0$/, "") + "万" : Number(value).toLocaleString("zh-CN");
  }
  function animate(element) {
    if (reduced.matches || !element.animate) return;
    return element.animate([{opacity:.6,transform:"translateY(5px)"},{opacity:1,transform:"none"}], {duration:500,easing:"ease-out"});
  }
  function toast(message) {
    clearTimeout(state.toastTimer); $("toast").textContent = message; $("toast").hidden = false;
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3200);
  }
  function navigate(tab) { if (location.hash !== "#" + tab) location.hash = tab; else renderRoute(); }
  function renderRoute() {
    if (state.tab) state.scrolls[state.tab] = $("app-scroll").scrollTop;
    const route = location.hash.slice(1) || "search";
    state.tab = route.startsWith("tasks/") ? "detail" : ["search","radar","bags","settings"].includes(route) ? route : "search";
    state.detailId = state.tab === "detail" ? route.slice(6) : "";
    document.querySelectorAll("[data-page]").forEach(page => { page.hidden = page.dataset.page !== state.tab; });
    const labels = {search:["一本好书，随身带走","书包"],radar:["发现下一本","番茄雷达"],bags:["找到的，正在来的","我的书包"],settings:["调成你喜欢的样子","设置"],detail:["书名与作者，始终保留","任务详情"]};
    [$("page-eyebrow").textContent,$("page-title").textContent] = labels[state.tab];
    $("page-back").hidden = state.tab !== "detail";
    $("header-action").hidden = state.tab === "detail" || state.tab === "settings";
    $("header-action").innerHTML = icon(state.tab === "radar" ? "refresh" : "plus");
    $("header-action").setAttribute("aria-label", state.tab === "radar" ? "刷新榜单说明" : "导入书单");
    document.querySelectorAll(".bottom-nav [data-nav]").forEach(button => {
      if (button.dataset.nav === (state.tab === "detail" ? "bags" : state.tab)) button.setAttribute("aria-current","page"); else button.removeAttribute("aria-current");
    });
    if (state.tab === "radar") renderRadar();
    if (state.tab === "bags") renderBags();
    if (state.tab === "detail") { detailSignature = ""; renderTask(); }
    $("app-scroll").scrollTop = state.tab === "detail" ? 0 : state.scrolls[state.tab] || 0;
    syncSheetPosition();
  }
  function bookRow(book, action = "pick") {
    return `<button class="book-row glass" type="button" data-${action}="${esc(book.book_id)}"><span class="book-mark" aria-hidden="true">${esc([...book.title][0])}</span><span class="book-copy"><strong>${esc(book.title)}</strong><small>${esc(book.author)} · ${esc(book.category)}</small><small class="book-platform">番茄</small></span>${icon("arrow")}</button>`;
  }
  function searchBooks(value) {
    const query = normalize(value);
    if (!query) return [];
    return allBooks.filter(book => normalize(book.title + " " + book.author).includes(query))
      .sort((a,b) => Number(normalize(b.title) === query) - Number(normalize(a.title) === query) || b.reads-a.reads);
  }
  function renderSearch() {
    const query = $("book-query").value.trim();
    document.querySelector(".search-page").classList.toggle("is-querying", !!query);
    $("search-welcome").hidden = !!query;
    $("search-status").hidden = !query;
    $("search-status").classList.toggle("is-error", !state.connected);
    if (!query) { $("search-results").replaceChildren(); return; }
    if (!state.connected) {
      $("search-status").textContent = "演示连接已中断，书名仍保留。";
      $("search-results").innerHTML = '<button class="secondary-button" type="button" data-restore-connection>恢复连接并重试</button>'; return;
    }
    const books = searchBooks(query);
    $("search-status").textContent = books.length ? "从已保存的快照中找到以下作品，选中后继续。" : "快照中没有匹配的书名或作者。";
    $("search-results").innerHTML = books.slice(0,8).map(book => bookRow(book)).join("") || `<div class="empty-state">${icon("search")}<strong>这次还没有找到</strong><p>试试完整书名，或回雷达看看。<br>原型仅检索随包快照。</p><button class="secondary-button" type="button" data-clear-search>重新输入</button></div>`;
  }
  function filteredBooks() {
    const query = normalize(state.q);
    return board().items.filter(book => (!query || normalize(book.title + book.author).includes(query))
      && (!state.category || book.category === state.category)
      && (!state.status || (state.status === "done" ? /完结/.test(book.status) : /连载/.test(book.status)))
      && book.reads >= state.minReads)
      .sort(state.sort === "pos" ? (a,b) => a.rank_pos-b.rank_pos || b.reads-a.reads : (a,b) => b.reads-a.reads || a.rank_pos-b.rank_pos);
  }
  function radarCard(book) {
    return `<article class="radar-book glass"><div class="radar-number" aria-label="分类内第 ${book.rank_pos} 名">${book.rank_pos}</div><div class="radar-copy"><h3>${esc(book.title)}</h3><p>${esc(book.author)} · ${esc(book.category)}</p><div class="radar-book-bottom"><strong>${count(book.reads)}</strong><span>在读 · ${esc(book.status || "状态未提供")}</span></div></div><button class="select-book" type="button" data-pick="${esc(book.book_id)}" aria-label="搜索《${esc(book.title)}》，作者 ${esc(book.author)}">${icon("arrow")}</button></article>`;
  }
  function renderRadar(append = false) {
    const books = filteredBooks(), source = board();
    document.querySelectorAll("[data-channel]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.channel === state.channel)));
    document.querySelectorAll("[data-rank]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.rank === state.rank)));
    $("snapshot-date").textContent = "快照 " + source.date.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
    const amount = Number(!!state.category)+Number(!!state.status)+Number(state.minReads>0)+Number(state.sort!=="reads");
    $("filter-count").hidden = !amount; $("filter-count").textContent = amount;
    $("clear-filters").hidden = !(amount || state.q);
    $("radar-summary").textContent = `${books.length} 条榜单记录${state.category ? " · " + state.category : ""}`;
    if (append) {
      const previous = $("radar-list").querySelectorAll(".radar-book").length;
      $("radar-list").insertAdjacentHTML("beforeend", books.slice(previous,state.limit).map(radarCard).join(""));
    } else {
      resultsAnimation?.cancel();
      $("radar-list").innerHTML = books.slice(0,state.limit).map(radarCard).join("") || `<div class="empty-state">${icon("radar")}<strong>没有符合条件的书</strong><p>保留当前频道，试试清除筛选。</p><button type="button" class="secondary-button" data-reset-filters>清除筛选</button></div>`;
      resultsAnimation = animate($("radar-list"));
    }
    $("radar-more").hidden = state.limit >= books.length;
    $("radar-more").textContent = `加载更多（${Math.min(state.limit,books.length)} / ${books.length}）`;
    $("radar-heat").innerHTML = source.heat.map(row => `<button type="button" data-heat="${esc(row.category)}" aria-pressed="${state.category===row.category}">${esc(row.category)}<span>${count(row.total_reads)}</span></button>`).join("");
    $("radar-hotwords").innerHTML = source.hotwords.map(row => `<button type="button" data-word="${esc(row.word)}">${esc(row.word)}<span>${row.count}</span></button>`).join("");
  }
  function resetFilters() { Object.assign(state,{category:"",status:"",minReads:0,sort:"reads",q:"",limit:20}); $("radar-query").value=""; renderRadar(); }
  function syncSheetPosition() {
    const rect = $("mobile-app").getBoundingClientRect(), edge = innerWidth > 980 ? 5 : 0;
    $("bottom-sheet").style.setProperty("--shell-left",rect.left + edge + "px");
    $("bottom-sheet").style.setProperty("--shell-width",rect.width - edge*2 + "px");
    $("bottom-sheet").style.setProperty("--shell-bottom",Math.max(0,innerHeight-rect.bottom+edge) + "px");
  }
  function openSheet(title, content, footer) {
    if (sheetClosing) return;
    $("sheet-title").textContent=title; $("sheet-content").innerHTML=content; $("sheet-footer").innerHTML=footer || '<button class="primary-button" type="button" data-close-sheet>知道了</button>';
    syncSheetPosition(); if (!$("bottom-sheet").open) $("bottom-sheet").showModal();
  }
  function closeSheet() {
    const sheet = $("bottom-sheet");
    if (!sheet.open) return Promise.resolve(); if (sheetClosing) return sheetClosing;
    sheet.classList.add("is-closing"); sheet.inert=true;
    sheetClosing = Promise.allSettled(sheet.getAnimations().filter(a => a.animationName==="sheet-leave").map(a => a.finished)).then(() => {
      sheet.close(); sheet.classList.remove("is-closing"); sheet.inert=false; sheetClosing=null;
    });
    return sheetClosing;
  }
  function openFilters() {
    const categories = [...new Set(board().items.map(book=>book.category))];
    openSheet("筛选榜单", `<label class="filter-field">分类<select id="filter-category"><option value="">全部分类</option>${categories.map(category=>`<option value="${esc(category)}" ${state.category===category?"selected":""}>${esc(category)}</option>`).join("")}</select></label>
      <label class="filter-field">作品状态<select id="filter-status"><option value="">全部状态</option><option value="done" ${state.status==="done"?"selected":""}>已完结</option><option value="serial" ${state.status==="serial"?"selected":""}>连载中</option></select></label>
      <label class="filter-field">最低在读<select id="filter-min"><option value="0">不限在读</option><option value="100000" ${state.minReads===100000?"selected":""}>10 万在读以上</option><option value="500000" ${state.minReads===500000?"selected":""}>50 万在读以上</option></select></label>
      <label class="filter-field">排序<select id="filter-sort"><option value="reads">在读优先</option><option value="pos" ${state.sort==="pos"?"selected":""}>分类内名次优先</option></select></label>`,
      '<button class="secondary-button" id="reset-filter-draft" type="button">重置</button><button class="primary-button" id="apply-filters" type="button">查看结果</button>');
  }
  function startTask(book) {
    if (!book) return;
    if (!state.connected) { toast("演示连接已中断，可到设置恢复连接。"); return; }
    if (active()) { toast("已有演示任务进行中，可到书包查看。"); return; }
    const task={id:"demo-"+Date.now(),book:{book_id:book.book_id,title:book.title,author:book.author,category:book.category},phase:"finding",startedAt:Date.now(),elapsed:0,progress:0,exported:false};
    state.tasks.unshift(task); state.returnTab=state.tab === "detail" ? "bags" : state.tab; persist(); closeSheet(); navigate("tasks/"+task.id); updateIndicator();
  }
  function updateIndicator() { $("task-indicator").hidden = !active(); }
  const phaseName = {finding:"正在找书",getting:"正在获取",packing:"正在打包",complete:"演示书包已生成",cancelled:"已停止，可继续",error:"连接中断，可重试"};
  function renderBags() {
    document.querySelectorAll("[data-bag-filter]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.bagFilter===state.bagFilter)));
    const tasks=state.tasks.filter(task=>state.bagFilter!=="complete"||task.phase==="complete");
    $("bag-list").innerHTML=tasks.map(task=>`<button class="bag-card glass" type="button" data-task="${esc(task.id)}"><div class="bag-card-head"><span>演示任务</span><span class="bag-card-status">${task.phase==="complete"?icon("check"):""}${phaseName[task.phase]}</span></div><h3>${esc(task.book.title)}</h3><p>${esc(task.book.author)} · 番茄</p>${task.phase==="complete"?'<p>原创演示 ZIP · 可保存到此设备</p>':`<div class="bag-progress"><span style="width:${task.progress||0}%"></span></div>`}</button>`).join("") || `<div class="empty-state">${icon("bag")}<strong>${state.bagFilter==="complete"?"还没有生成的书包":"书包里，先留一点空位"}</strong><p>找到喜欢的书之后，任务会留在这里。</p><button class="secondary-button" type="button" data-nav="radar">去雷达选一本</button></div>`;
  }
  function renderTask() {
    const task=state.tasks.find(item=>item.id===state.detailId);
    if (!task) { $("task-detail").innerHTML='<div class="empty-state"><strong>没有找到这条演示任务</strong><button class="secondary-button" type="button" data-nav="bags">返回书包</button></div>'; return; }
    const signature=task.id+"/"+task.phase;
    if (signature!==detailSignature) {
      const index=task.phase==="finding"?0:task.phase==="packing"?2:task.phase==="complete"?3:1;
      const complete=task.phase==="complete", stopped=["cancelled","error"].includes(task.phase);
      $("task-detail").innerHTML=`<section class="task-card glass" data-phase="${task.phase}"><div class="task-book"><span class="book-mark" aria-hidden="true">${esc([...task.book.title][0])}</span><div><h3>${esc(task.book.title)}</h3><p>${esc(task.book.author)} · 番茄</p></div></div><p class="task-stage-label">${complete?icon("check"):'<span class="status-bubble" aria-hidden="true"></span>'}<span>${phaseName[task.phase]}</span></p><p class="task-description">${complete?"演示文件已准备好，可以保存或分享。":stopped?"书名、作者和已完成的演示阶段均已保留。":"你可以切到雷达或其他页面，任务仍留在书包里。"}</p><div class="task-progress ${task.phase==="finding"||task.phase==="packing"?"is-indeterminate":""}" role="progressbar" aria-label="演示获取进度" aria-valuemin="0" aria-valuemax="100" ${task.phase==="getting"?`aria-valuenow="${task.progress}"`:""}><span id="detail-fill" style="width:${task.progress||0}%"></span></div><p class="task-progress-caption"><span id="detail-progress-copy"></span><span>演示进度</span></p><ol class="task-stages" aria-label="任务阶段">${["找书","获取","打包","完成"].map((name,i)=>`<li class="${i<index||complete?"is-done":i===index?"is-current":""}"><i aria-hidden="true"></i>${name}</li>`).join("")}</ol><div class="task-actions">${complete?`<button class="primary-button" type="button" data-export="${esc(task.id)}">${icon("download")}保存演示书包</button><button class="secondary-button" type="button" data-share="${esc(task.id)}">${icon("share")}分享演示文件</button>`:stopped?`<button class="primary-button" type="button" data-resume="${esc(task.id)}">继续演示任务</button>`:`<button class="secondary-button" type="button" data-stop="${esc(task.id)}">停止演示任务</button>`}</div><p class="file-note">${complete?"文件包含原创样文《雨后的借书处》及演示说明，不是这本榜单作品的正文。":"此处用于体验获取流程，未连接真实书源。"}</p></section><div class="task-extra glass">${icon("book")}<span>${complete?"点击保存后，文件位置以系统提示为准；取消分享不会影响已生成的书包。":"切换页面不会停止任务。页面重新打开时，这条演示记录仍会保留。"}</span></div>`;
      detailSignature=signature;
    }
    $("detail-fill").style.width=(task.phase==="complete"?100:task.progress||0)+"%";
    const meter=$("task-detail").querySelector('[role="progressbar"]');
    if (["getting","complete"].includes(task.phase)) meter.setAttribute("aria-valuenow",task.progress||0); else meter.removeAttribute("aria-valuenow");
    $("detail-progress-copy").textContent=task.phase==="complete"?"原创演示 ZIP":task.phase==="getting"?"正在演示正文获取 · "+task.progress+"%":task.phase==="packing"?"正在整理演示文件":task.phase==="finding"?"正在演示作品匹配":"演示阶段已保留";
  }
  function tick() {
    let changed=false;
    for (const task of state.tasks) {
      if (["complete","cancelled","error"].includes(task.phase)) continue;
      const elapsed=task.elapsed+Date.now()-task.startedAt, previous=task.phase;
      task.phase=elapsed<750?"finding":elapsed<5250?"getting":elapsed<6200?"packing":"complete";
      task.progress=task.phase==="complete"?100:Math.min(99,Math.max(0,Math.round((elapsed-750)/4500*100)));
      if (previous!==task.phase) { changed=true; if (task.phase==="complete") toast("演示书包已生成，可以到书包保存。"); }
    }
    if (changed) { persist(); updateIndicator(); }
    if (state.tab==="detail") renderTask();
    if (state.tab==="bags") {
      if (changed) renderBags();
      else for (const task of state.tasks) {
        const fill=$("bag-list").querySelector(`[data-task="${CSS.escape(task.id)}"] .bag-progress span`);
        if (fill) fill.style.width=(task.progress||0)+"%";
      }
    }
  }
  function applyAppearance() {
    const settings=state.settings;
    $("mobile-app").dataset.preset=settings.auto?(new Date().getHours()<10?"morning":new Date().getHours()>=17?"sunset":"afternoon"):settings.preset;
    $("mobile-app").dataset.still=String(!settings.motion||reduced.matches||document.hidden);
    document.documentElement.style.setProperty("--film",settings.film/100);
    $("app-scene").style.animationDuration=(24-settings.waves*.15)+"s";
    $("app-scene").style.setProperty("--wave-travel",settings.waves*.08+"px");
    $("app-scene").style.setProperty("--wave-scale",1.04+settings.waves*.00035);
    $("app-scene").style.filter=`brightness(${.82+settings.daylight*.004})`;
    for (const name of ["waves","daylight","film"]) { $(name).value=settings[name]; $(name+"-value").textContent=settings[name]+(name==="film"?"%":""); }
    document.querySelectorAll(".ocean-presets [data-preset]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.preset===settings.preset&&!settings.auto)));
    $("toggle-motion").setAttribute("aria-pressed",String(settings.motion)); $("toggle-auto").setAttribute("aria-pressed",String(settings.auto));
    $("daylight").disabled=settings.auto;
  }
  function importSheet() {
    openSheet("导入书单", '<p>一行一本，可写“书名 | 作者”。原型会从离线快照匹配作品。</p><label class="file-input-label">选择 TXT / CSV / JSON 书单<input type="file" id="booklist-file" accept=".txt,.csv,.json,text/plain,text/csv,application/json"></label><label class="sr-only" for="import-text">逐行填写书名</label><textarea class="import-text" id="import-text" placeholder="神通者 | 天蚕土豆\n十日终焉 | 杀虫队队员"></textarea><div id="import-results" class="import-results"></div>', '<button class="secondary-button" type="button" data-close-sheet>取消</button><button class="primary-button" type="button" id="preview-import">预览书单</button>');
  }
  function parseCsv(text) {
    const rows=[];let row=[],cell="",quoted=false;
    for(let i=0;i<text.length;i++) {
      const char=text[i];
      if(char==='"') {
        if(quoted&&text[i+1]==='"'){cell+='"';i++;}
        else if(quoted||!cell) quoted=!quoted;
        else cell+=char;
      } else if(char===","&&!quoted) {row.push(cell);cell="";}
      else if((char==="\n"||char==="\r")&&!quoted) {
        row.push(cell);if(row.some(value=>value.trim()))rows.push(row);row=[];cell="";
        if(char==="\r"&&text[i+1]==="\n")i++;
      } else cell+=char;
    }
    if(quoted)throw new Error("CSV 引号未结束");
    row.push(cell);if(row.some(value=>value.trim()))rows.push(row);
    if(rows[0]&&["书名","title"].includes(normalize(rows[0][0])))rows.shift();
    return rows.map(row=>row.slice(0,2).map(value=>value.trim()).join(" | ")).join("\n");
  }
  function crc32(bytes) { let crc=0xffffffff; for (const byte of bytes) { crc^=byte; for(let bit=0;bit<8;bit++) crc=(crc>>>1)^((crc&1)?0xedb88320:0); } return (crc^0xffffffff)>>>0; }
  function demoZip(task) {
    const encoder=new TextEncoder(), name=encoder.encode("原创演示.txt"), content=encoder.encode(`这是书包移动端交互原型的演示文件。\n本次界面选中：《${task.book.title}》 / ${task.book.author}\n下文不是该作品正文。\n\n${data.demoText}`);
    const crc=crc32(content), local=new Uint8Array(30+name.length), central=new Uint8Array(46+name.length), end=new Uint8Array(22);
    const l=new DataView(local.buffer), c=new DataView(central.buffer), e=new DataView(end.buffer);
    l.setUint32(0,0x04034b50,true);l.setUint16(4,20,true);l.setUint16(6,0x800,true);l.setUint16(12,33,true);l.setUint32(14,crc,true);l.setUint32(18,content.length,true);l.setUint32(22,content.length,true);l.setUint16(26,name.length,true);local.set(name,30);
    c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x800,true);c.setUint16(14,33,true);c.setUint32(16,crc,true);c.setUint32(20,content.length,true);c.setUint32(24,content.length,true);c.setUint16(28,name.length,true);central.set(name,46);
    e.setUint32(0,0x06054b50,true);e.setUint16(8,1,true);e.setUint16(10,1,true);e.setUint32(12,central.length,true);e.setUint32(16,local.length+content.length,true);
    return new Blob([local,content,central,end],{type:"application/zip"});
  }
  function exportTask(task) {
    const url=URL.createObjectURL(demoZip(task)), link=document.createElement("a"); link.href=url;link.download="书包交互演示.zip";link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
    toast("已发起演示 ZIP 保存；保存位置以浏览器提示为准。");
  }
  async function shareTask(task) {
    const file=new File([demoZip(task)],"书包交互演示.zip",{type:"application/zip"});
    if (!navigator.canShare?.({files:[file]})) { toast("此预览环境不支持系统文件分享，可先保存演示书包。"); return; }
    try { await navigator.share({title:"书包交互演示",files:[file]}); } catch (error) { if(error.name!=="AbortError") toast("分享未完成，可改为保存演示书包。"); }
  }
  document.addEventListener("click", event => {
    const button=event.target.closest("button"); if(!button||button.disabled) return;
    if(button.dataset.nav) { closeSheet(); navigate(button.dataset.nav); }
    if(button.hasAttribute("data-pick")) startTask(findBook(button.dataset.pick));
    if(button.dataset.task) { state.returnTab="bags"; navigate("tasks/"+button.dataset.task); }
    if(button.dataset.channel) { state.channel=button.dataset.channel;state.category="";state.limit=20;renderRadar();$("app-scroll").scrollTop=0; }
    if(button.dataset.rank) { state.rank=button.dataset.rank;state.category="";state.limit=20;renderRadar();$("app-scroll").scrollTop=0; }
    if(button.dataset.heat) { state.category=state.category===button.dataset.heat?"":button.dataset.heat;state.limit=20;renderRadar(); }
    if(button.dataset.word) { state.q=button.dataset.word;$("radar-query").value=state.q;state.limit=20;renderRadar(); }
    if(button.hasAttribute("data-reset-filters")) resetFilters();
    if(button.hasAttribute("data-close-sheet")) closeSheet();
    if(button.dataset.bagFilter) { state.bagFilter=button.dataset.bagFilter;renderBags(); }
    if(button.dataset.preset) { Object.assign(state.settings,{preset:button.dataset.preset,auto:false,daylight:button.dataset.preset==="morning"?35:button.dataset.preset==="sunset"?20:55});applyAppearance();persist(); }
    if(button.hasAttribute("data-clear-search")) { $("book-query").value="";renderSearch();$("book-query").focus(); }
    if(button.hasAttribute("data-restore-connection")) {state.connected=true;renderSearch();toast("演示连接已恢复。");}
    const id=button.dataset.stop||button.dataset.resume||button.dataset.export||button.dataset.share;
    if(id) {
      const task=state.tasks.find(item=>item.id===id);if(!task)return;
      if(button.dataset.stop) { task.elapsed+=Date.now()-task.startedAt;task.phase="cancelled";persist();renderTask();updateIndicator(); }
      if(button.dataset.resume) { if(active()){toast("已有任务进行中。");return;}task.startedAt=Date.now();task.phase="getting";persist();tick();updateIndicator(); }
      if(button.dataset.export) exportTask(task);
      if(button.dataset.share) shareTask(task);
    }
    if(button.id==="apply-filters") { Object.assign(state,{category:$("filter-category").value,status:$("filter-status").value,minReads:Number($("filter-min").value),sort:$("filter-sort").value,limit:20});renderRadar();$("app-scroll").scrollTop=0;closeSheet(); }
    if(button.id==="reset-filter-draft") { for(const [id,value] of [["filter-category",""],["filter-status",""],["filter-min","0"],["filter-sort","reads"]])$(id).value=value; }
    if(button.id==="preview-import") {
      const rows=$("import-text").value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
      $("import-results").innerHTML=rows.slice(0,30).map(line=>{const [title,author]=line.split(/[|\t]/).map(s=>s.trim());const book=searchBooks(title).find(item=>!author||normalize(item.author)===normalize(author));return book?bookRow(book):`<p class="inline-status">${esc(line)}：当前离线快照未找到。</p>`;}).join("")||'<p class="inline-status">先填写至少一个书名。</p>';
    }
    if(button.id==="demo-disconnect") {state.connected=false;closeSheet();toast("已切换为演示连接中断状态，书名和任务保留。");}
    if(button.id==="demo-reconnect") {state.connected=true;closeSheet();toast("演示连接已恢复。");}
  });
  document.addEventListener("pointerdown", event => {
    const button=event.target.closest("button");if(!button||button.disabled||reduced.matches||event.button!==0)return;
    button.querySelectorAll(".tap-ripple").forEach(node=>node.remove());
    const rect=button.getBoundingClientRect(),size=Math.hypot(rect.width,rect.height)*2,wave=document.createElement("span");
    wave.className="tap-ripple";wave.setAttribute("aria-hidden","true");Object.assign(wave.style,{width:size+"px",height:size+"px",left:event.clientX-rect.left-size/2+"px",top:event.clientY-rect.top-size/2+"px"});button.prepend(wave);setTimeout(()=>wave.remove(),500);
  });
  $("search-form").addEventListener("submit",event=>{event.preventDefault();if(composing)return;clearTimeout(searchTimer);renderSearch();const books=searchBooks($("book-query").value);if(state.connected&&books.length===1)startTask(books[0]);});
  $("book-query").addEventListener("compositionstart",()=>{composing=true;clearTimeout(searchTimer);});
  $("book-query").addEventListener("compositionend",()=>{composing=false;renderSearch();});
  $("book-query").addEventListener("input",event=>{clearTimeout(searchTimer);if(event.isComposing||composing)return;searchTimer=setTimeout(renderSearch,250);});
  $("radar-query").addEventListener("input",event=>{if(event.isComposing)return;state.q=event.target.value;state.limit=20;renderRadar();});
  $("radar-query").addEventListener("compositionend",event=>{state.q=event.target.value;state.limit=20;renderRadar();});
  $("radar-more").addEventListener("click",()=>{state.limit+=20;renderRadar(true);});
  $("open-filters").addEventListener("click",openFilters);$("clear-filters").addEventListener("click",resetFilters);
  $("close-sheet").addEventListener("click",closeSheet);
  $("bottom-sheet").addEventListener("cancel",event=>{event.preventDefault();closeSheet();});
  $("bottom-sheet").addEventListener("click",event=>{if(event.target!==$("bottom-sheet"))return;const rect=event.target.getBoundingClientRect();if(event.clientY<rect.top||event.clientX<rect.left||event.clientX>rect.right)closeSheet();});
  $("page-back").addEventListener("click",()=>navigate(state.returnTab||"bags"));
  $("header-action").addEventListener("click",()=>state.tab==="radar"?openSheet("已保存的榜单",'<p>当前使用 2026-10-13 的随包快照。原型不进行在线刷新。</p><p>正式版由执行服务刷新当前频道，并显示真实分类进度；失败时仍保留上次快照。</p>'):importSheet());
  $("settings-sources").addEventListener("click",()=>openSheet("书源与文件",'<p>正式版沿用书包的现有书源、换源与缓存。手机文件通过系统文件选择器导入，电脑目录不会自动变成手机目录。</p><button class="secondary-button" type="button" id="open-import-from-settings">体验书单导入</button>'));
  $("sheet-content").addEventListener("click",event=>{if(event.target.closest("#open-import-from-settings"))importSheet();});
  $("connection-settings").addEventListener("click",()=>openSheet("连接状态预览",'<p>当前为不联网的交互原型。可切换演示状态，查看书名保留和原位重试。</p><div class="connection-actions"><button class="secondary-button" id="demo-disconnect" type="button">演示连接中断</button><button class="primary-button" id="demo-reconnect" type="button">恢复演示连接</button></div>'));
  $("toggle-motion").addEventListener("click",()=>{state.settings.motion=!state.settings.motion;applyAppearance();persist();});
  $("toggle-auto").addEventListener("click",()=>{state.settings.auto=!state.settings.auto;applyAppearance();persist();});
  $("reset-appearance").addEventListener("click",()=>{state.settings={...defaults};applyAppearance();persist();toast("已恢复默认外观。");});
  for(const name of ["waves","daylight","film"])$(name).addEventListener("input",event=>{state.settings[name]=Number(event.target.value);applyAppearance();persist();});
  $("sheet-content").addEventListener("change",async event=>{
    if(event.target.id!=="booklist-file"||!event.target.files[0])return;
    const file=event.target.files[0];if(file.size>1024*1024){toast("预览书单请控制在 1 MB 内。");return;}
    try {let text=(await file.text()).replace(/^\uFEFF/,"");if(file.name.toLowerCase().endsWith(".json")){const payload=JSON.parse(text);const rows=Array.isArray(payload)?payload:payload.books||payload.tasks||[];text=rows.map(row=>typeof row==="string"?row:[row.title||row.name,row.author].filter(Boolean).join(" | ")).join("\n");}else if(file.name.toLowerCase().endsWith(".csv")){text=parseCsv(text);}$("import-text").value=text;}catch{toast("这份书单未能读取，请检查内容格式。");}
  });
  addEventListener("hashchange",renderRoute);addEventListener("resize",syncSheetPosition);
  window.visualViewport?.addEventListener("resize",syncSheetPosition);
  document.addEventListener("visibilitychange",()=>{applyAppearance();if(!document.hidden)tick();});
  reduced.addEventListener("change",()=>{resultsAnimation?.cancel();document.querySelectorAll(".tap-ripple").forEach(node=>node.remove());applyAppearance();});
  $("inspiration-list").innerHTML=[...data.boards["male/read"].items].sort((a,b)=>b.reads-a.reads).slice(0,2).map(book=>bookRow(book)).join("");
  applyAppearance();renderRoute();updateIndicator();tick();setInterval(tick,250);
})();
