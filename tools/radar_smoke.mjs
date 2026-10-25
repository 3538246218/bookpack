// Exercise the actual radar UI and title/author handoff in an isolated local app.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "radar-" + Date.now());
const profile = path.join(run, "chrome-profile");
fs.mkdirSync(run, { recursive: true });
let service, chrome, socket, origin, output = "";
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await callback();
    if (result) return result;
    await delay(100);
  }
  throw new Error("Timed out: " + label);
}
async function api(route, data) {
  const response = await fetch(origin + route, data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
  });
  const payload = await response.json();
  assert.ok(response.ok, JSON.stringify(payload));
  return payload;
}
try {
  service = spawn("python", ["-X", "utf8", "app.py", "--home", run, "--port", "0"], {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"]
  });
  service.stdout.on("data", bytes => { output += bytes.toString("utf8"); });
  service.stderr.on("data", bytes => { output += bytes.toString("utf8"); });
  origin = await until(() => output.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0], "application ready");
  const seed = (await api("api/radar?channel=male&rank=read")).items[0];
  await api("api/sources", { local_dirs: [], wikisource: false, web_sources: [], books: [{
    title: seed.title, author: seed.author, source: "内置原创演示", url: origin + "demo/original.txt"
  }] });
  chrome = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", [
    "--headless=new", "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=0", "--user-data-dir=" + profile, "--window-size=1440,1000", "about:blank"
  ], { windowsHide: true, stdio: "ignore" });
  const active = path.join(profile, "DevToolsActivePort");
  const port = await until(() => fs.existsSync(active) && fs.readFileSync(active, "utf8").split("\n")[0], "browser ready");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
  socket = new WebSocket(targets.find(row => row.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let counter = 0;
  const requests = new Map(), errors = [];
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (requests.has(message.id)) {
      const request = requests.get(message.id); requests.delete(message.id);
      message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++counter; requests.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result?.value;
  }
  const click = id => evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
  async function ready() {
    await until(() => evaluate('document.getElementById("radar-books").getAttribute("aria-busy")==="false" && !!document.querySelector(".radar-card")'), "rank cards ready");
  }
  async function query(value) {
    await evaluate(`document.getElementById("radar-query").value=${JSON.stringify(value)};document.getElementById("radar-query").dispatchEvent(new Event("input",{bubbles:true}));`);
    await delay(350);
    await until(() => evaluate('document.getElementById("radar-books").getAttribute("aria-busy")==="false"'), "query ready");
  }
  async function screenshot(filename) {
    await delay(250);
    const result = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    fs.writeFileSync(path.join(root, "预览", filename), Buffer.from(result.data, "base64"));
  }
  await command("Runtime.enable"); await command("Page.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: origin });
  await until(() => evaluate('document.readyState==="complete" && !document.getElementById("book-title").disabled'), "main UI ready");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "ocean ready", 45000);
  await evaluate(`window.__feedbackTest={delay:650,selectDelay:900,startDelay:900,failRadarOnce:false,failStart:false,refresh:null};
    window.__feedbackOriginalFetch=window.fetch.bind(window);
    window.fetch=async(...args)=>{
      const route=String(args[0]),test=window.__feedbackTest;
      const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
      if(route==='/api/start'&&test.failStart)return json({error:'演示连接暂时中断，请重试。'},503);
      if(route==='/api/radar/refresh'&&test.refresh)return json(test.refresh,202);
      if(route==='/api/radar/status'&&test.refresh)return json(test.refresh);
      if(route.startsWith('/api/radar?')&&test.failRadarOnce){test.failRadarOnce=false;await new Promise(resolve=>setTimeout(resolve,test.delay));return json({error:'演示榜单暂时不可用。'},503);}
      let response=await window.__feedbackOriginalFetch(...args);
      if(route.startsWith('/api/radar?')&&test.refresh){const body=await response.json();body.refresh=test.refresh;response=json(body);}
      const duration=route.startsWith('/api/radar?')?test.delay:route==='/api/radar/select'?test.selectDelay:route==='/api/start'?test.startDelay:0;
      if(duration)await new Promise(resolve=>setTimeout(resolve,duration));
      return response;
    };`);
  const launcher = await evaluate('(()=>{const r=document.getElementById("radar-open").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()');
  assert.equal(await evaluate('getComputedStyle(document.getElementById("radar-open")).position'), "fixed");
  assert.ok(await evaluate('document.getElementById("radar-open").getBoundingClientRect().right>innerWidth-60'));
  await command("Input.dispatchMouseEvent", { type: "mouseMoved", ...launcher });
  await command("Input.dispatchMouseEvent", { type: "mousePressed", ...launcher, button: "left", buttons: 1, clickCount: 1 });
  await delay(170);
  assert.ok(await evaluate('!!document.querySelector("#radar-open > .tap-ripple")'));
  const pressFeedback = await evaluate('(()=>{const b=document.getElementById("radar-open"),s=getComputedStyle(b);return {transform:s.transform,active:b.matches(":active"),transition:s.transition,reduced:matchMedia("(prefers-reduced-motion: reduce)").matches}})()');
  await until(() => evaluate('getComputedStyle(document.getElementById("radar-open")).transform.includes("0.985")'), "press feedback " + JSON.stringify(pressFeedback), 3000);
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", ...launcher, button: "left", buttons: 0, clickCount: 1 });
  await delay(60);
  assert.equal(await evaluate('getComputedStyle(document.getElementById("radar-dialog")).animationDuration'), "0.5s");
  assert.equal(await evaluate('getComputedStyle(document.getElementById("radar-dialog"),"::backdrop").animationDuration'), "0.5s");
  assert.equal(await evaluate('document.querySelectorAll(".radar-skeleton").length'), 6);
  assert.equal(await evaluate('document.getElementById("radar-loading").hidden'), false);
  await screenshot("反馈首次加载.png");
  await ready();
  assert.ok(await evaluate('document.getElementById("radar-books").getAnimations().some(animation=>animation.effect.getTiming().duration===500)'));
  assert.equal(await evaluate('document.querySelectorAll(".radar-card").length'), 30);
  assert.match(await evaluate('document.getElementById("radar-summary").textContent'), /2026-10-13/);
  assert.equal(await evaluate('document.getElementById("radar-error").hidden'), true);
  await screenshot("番茄雷达.png");
  await click("radar-more"); await ready();
  assert.equal(await evaluate('document.querySelectorAll(".radar-card").length'), 60);
  await evaluate('window.__feedbackKeptCard=document.querySelector(".radar-card");document.querySelector("[data-radar-channel=female]").click()');
  await delay(60);
  assert.equal(await evaluate('window.__feedbackKeptCard===document.querySelector(".radar-card")'), true);
  assert.equal(await evaluate('document.getElementById("radar-books").inert'), true);
  assert.ok(await evaluate('document.getElementById("radar-loading-label").textContent.includes("暂显上次结果")'));
  await screenshot("反馈筛选等待.png");
  await ready();
  assert.ok(await evaluate('document.getElementById("radar-books").getAnimations().some(animation=>animation.effect.getTiming().duration===500)'));
  assert.match(await evaluate('document.getElementById("radar-summary").textContent'), /女频阅读榜/);
  await evaluate('document.querySelector("[data-radar-rank=new]").click()'); await ready();
  assert.match(await evaluate('document.getElementById("radar-summary").textContent'), /女频新书榜/);
  await evaluate('window.__feedbackTest.failRadarOnce=true;document.querySelector("[data-radar-rank=read]").click()');
  await until(() => evaluate('!document.getElementById("radar-reload").hidden'), "failed list exposes retry");
  assert.ok(await evaluate('!!document.querySelector(".radar-card") && document.getElementById("radar-error").textContent.includes("仍显示上次")'));
  await click("radar-reload"); await ready();
  await query("没有匹配的原创测试书名");
  assert.equal(await evaluate('document.querySelectorAll(".radar-card").length'), 0);
  assert.equal(await evaluate('document.getElementById("radar-empty").hidden'), false);
  await click("radar-reset"); await ready();
  await evaluate(`window.__feedbackTest.refresh={running:true,channel:'female',rank:'read',completed:4,total:18,books:40,message:'正在更新女频阅读榜 · 演示分类（5/18）',error:'',updated_at:''};document.getElementById('radar-refresh').click();`);
  await until(() => evaluate('!document.getElementById("radar-refresh-progress").hidden'), "refresh progress visible");
  assert.equal(await evaluate('document.getElementById("radar-refresh-progress").getAttribute("aria-valuenow")'), "22");
  assert.equal(await evaluate('document.getElementById("radar-refresh-spinner").hidden'), false);
  await evaluate(`Object.assign(window.__feedbackTest.refresh,{running:false,completed:18,books:180,message:'女频阅读榜已更新，18 个分类、180 条记录。',updated_at:'20261025'});`);
  await until(() => evaluate('document.getElementById("radar-progress").classList.contains("is-complete")'), "refresh completion feedback");
  await ready();
  await evaluate('window.__feedbackTest.refresh=null');
  await evaluate('document.querySelector("[data-radar-category]").click()'); await ready();
  const category = await evaluate('document.getElementById("radar-category").value');
  assert.ok(category);
  assert.ok(await evaluate(`Array.from(document.querySelectorAll(".radar-position")).every(row=>row.textContent.startsWith(${JSON.stringify(category)}))`));
  await click("radar-reset"); await ready();
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await screenshot("番茄雷达窄屏.png");
  assert.ok(await evaluate('document.getElementById("radar-dialog").scrollWidth<=document.getElementById("radar-dialog").clientWidth+1'));
  assert.ok(await evaluate('document.getElementById("radar-scroll").clientHeight>140'));
  await command("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  await command("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  assert.ok(await evaluate('document.getElementById("radar-dialog").getAnimations().some(animation=>animation.animationName==="radar-leave"&&animation.effect.getTiming().duration===500)'));
  await until(() => evaluate('!document.getElementById("radar-dialog").open'), "Escape closes dialog");
  assert.equal(await evaluate('document.activeElement.id'), "radar-open");
  await click("radar-open"); await ready();
  await evaluate('document.querySelector("[data-radar-channel=male]").click();document.querySelector("[data-radar-rank=read]").click()');
  await ready();
  await query(seed.author);
  await ready();
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate(`document.querySelector('[data-radar-select="${seed.book_id}"]').click()`);
  await delay(60);
  assert.equal(await evaluate('document.querySelector(".radar-select:disabled .radar-select-label")?.textContent'), "已选定");
  assert.ok(await evaluate('document.getElementById("radar-loading-label").textContent.includes("已选定")'));
  await screenshot("反馈选书衔接.png");
  await until(() => evaluate('document.getElementById("radar-dialog").classList.contains("is-closing")'), "selection starts exit transition");
  assert.equal(await evaluate('document.getElementById("radar-dialog").open'), true);
  assert.equal(await evaluate('document.getElementById("result").dataset.phase'), "finding");
  assert.ok(await evaluate('document.getElementById("radar-dialog").getAnimations().some(animation=>animation.animationName==="radar-leave"&&animation.effect.getTiming().duration===500)'));
  await until(() => evaluate('!document.getElementById("radar-dialog").open'), "selected book returns to search");
  assert.equal(await evaluate('document.getElementById("result").hidden'), false);
  assert.equal(await evaluate('document.getElementById("result").dataset.phase'), "finding");
  assert.ok(await evaluate(`document.getElementById('result-status').textContent.includes(${JSON.stringify(seed.title)})`));
  await screenshot("反馈搜索等待.png");
  const completed = await until(async () => {
    const snapshot = await api("api/state?full=1");
    return snapshot.batches.find(batch => batch.status === "complete");
  }, "selected book completes fixture download");
  assert.equal(completed.tasks[0].title, seed.title);
  assert.equal(completed.tasks[0].author, seed.author);
  assert.equal(completed.tasks[0].platform_label, "番茄");
  assert.ok(completed.archive);
  await until(() => evaluate('document.getElementById("result").dataset.phase==="complete"'), "completion checkmark");
  assert.equal(await evaluate('document.getElementById("state-dot").classList.contains("is-success")'), true);
  assert.equal(await evaluate('document.querySelectorAll("#task-stages .is-done").length'), 4);
  await screenshot("反馈获取完成.png");
  await evaluate(`window.__feedbackTest.failStart=true;document.getElementById('book-title').value='原创连接失败演示';document.getElementById('book-title').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('search-form').requestSubmit();`);
  await until(() => evaluate('!!document.querySelector("[data-search-again]")'), "start failure can retry in place");
  assert.ok(await evaluate('document.querySelector("#book-list .book-name").textContent.includes("原创连接失败演示")'));
  assert.equal(await evaluate('document.getElementById("book-title").disabled'), false);
  await evaluate(`window.__feedbackTest.failStart=false;document.getElementById('book-title').value=${JSON.stringify(seed.title + " | " + seed.author)};document.getElementById('book-title').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-search-again]').click();`);
  await until(async () => (await api("api/state")).batches.filter(batch => batch.status === "complete").length === 2, "retry completes fixture download");
  await until(() => evaluate('document.getElementById("result").dataset.phase==="complete"&&!document.getElementById("book-title").disabled'), "retry UI complete");
  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await click("radar-open"); await ready();
  assert.equal(await evaluate('document.querySelectorAll(".tap-ripple").length'), 0);
  assert.equal(await evaluate('getComputedStyle(document.getElementById("radar-dialog")).animationName'), "none");
  assert.equal(await evaluate('getComputedStyle(document.getElementById("radar-dialog"),"::backdrop").animationName'), "none");
  assert.equal(await evaluate('document.getElementById("radar-books").getAnimations().length'), 0);
  await click("radar-close");
  assert.equal(await evaluate('document.getElementById("radar-dialog").open'), false);
  assert.deepEqual(errors, []);
  const report = { origin, run, version: (await api("api/ping")).version, seed, batch: completed.id,
    desktop: "1440x1000", mobile: "390x844", motionMs: 500, checks: ["press and ripple", "500ms panel and backdrop entrance", "500ms results transition", "500ms panel exit", "search starts during exit transition", "first-load skeleton", "retained cards during filtering", "list error and inline retry", "refresh progress and completion", "dated snapshot", "four board filters", "pagination", "empty state", "category chips", "narrow layout", "Escape and focus", "selection feedback", "immediate named search placeholder", "title and author search handoff", "fixture archive", "completion checkmark and stages", "failed start and retry", "reduced motion"], errors };
  fs.writeFileSync(path.join(run, "result.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  socket?.close();
  chrome?.kill();
  if (origin) await fetch(origin + "api/shutdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  await delay(600);
  service?.kill();
}
