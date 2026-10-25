// A focused UI pass using original local text. No external sites or packages.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "sea-" + Date.now());
const profile = path.join(run, "chrome-profile");
const preview = path.join(root, "预览");
const appearanceOnly = process.argv.includes("--appearance-only");
fs.mkdirSync(run, { recursive: true });
fs.mkdirSync(preview, { recursive: true });
const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const pythonPath = "C:\\Users\\PC\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
let service, chrome, socket, slowServer;
let address = "", serviceError = "", stopped = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await callback();
    if (value) return value;
    await delay(100);
  }
  throw new Error("Timed out: " + label);
}
try {
  service = spawn(pythonPath, ["app.py", "--home", run, "--port", "0"], {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"]
  });
  service.stdout.on("data", data => { address += data.toString("utf8"); });
  service.stderr.on("data", data => { serviceError += data.toString("utf8"); });
  const origin = await until(() => address.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0], "server ready");
  async function api(route, data) {
    const response = await fetch(origin + route, data === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
    });
    const result = await response.json();
    assert.ok(response.ok, JSON.stringify(result));
    return result;
  }
  const config = { local_dirs: [], wikisource: false, books: [
    { title: "雨后的借书处", author: "原创演示", url: origin + "demo/original.txt", source: "本地演示书库" }
  ], web_sources: [] };
  await api("api/sources", config);
  const background = await fetch(origin + "ocean/frame.html");
  assert.ok(background.ok && background.headers.get("content-type").includes("text/html"));

  chrome = spawn(chromePath, ["--headless=new", "--enable-unsafe-swiftshader", "--no-first-run",
    "--no-default-browser-check", "--remote-debugging-port=0", "--user-data-dir=" + profile,
    "--window-size=1440,1100", "about:blank"], { windowsHide: true, stdio: "ignore" });
  const activePort = path.join(profile, "DevToolsActivePort");
  const port = await until(() => fs.existsSync(activePort) && fs.readFileSync(activePort, "utf8").split("\n")[0], "browser ready");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
  socket = new WebSocket(targets.find(item => item.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let counter = 0;
  const requests = new Map(), errors = [];
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.id && requests.has(message.id)) {
      const request = requests.get(message.id); requests.delete(message.id);
      message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++counter; requests.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result?.value;
  }
  const visibleCount = selector => evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].filter(el => el.getClientRects().length).length`);
  async function fillTitle(title) {
    await evaluate(`document.getElementById("book-title").value = ${JSON.stringify(title)}; document.getElementById("book-title").dispatchEvent(new Event("input", { bubbles: true }));`);
  }
  async function screenshot(name) {
    await delay(800);
    const shot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    fs.writeFileSync(path.join(preview, name), Buffer.from(shot.data, "base64"));
  }
  const oceanState = () => evaluate('document.getElementById("ocean-scene").contentWindow.__ossState()');
  async function setRange(id, value) {
    await evaluate(`document.getElementById(${JSON.stringify(id)}).value = ${value}; document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event("input", { bubbles: true }));`);
  }
  async function newBatch(title, status) {
    return until(async () => {
      const state = await api("api/state");
      return state.batches.find(batch => batch.tasks[0]?.title === title && batch.status === status);
    }, title + ": " + status);
  }

  await command("Runtime.enable"); await command("Page.enable");
  await command("Page.addScriptToEvaluateOnNewDocument", { source: `if (location.origin === ${JSON.stringify(origin.slice(0, -1))} && !localStorage.getItem("bookpack-ocean-settings-v1")) localStorage.setItem("bookpack-ocean-settings-v1", JSON.stringify({sea:61,daylight:48,glass:64,auto:false}));` });
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: origin });
  await until(() => evaluate('document.readyState === "complete" && !!document.getElementById("search-button")'), "UI loaded");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "live ocean first frame", 60000);
  assert.equal(await evaluate('document.getElementById("ocean-waves").value'), "61", "existing wave setting retained");
  assert.equal(await evaluate('document.getElementById("ocean-daylight").value'), "48", "existing sunlight retained");
  assert.equal(await evaluate('document.getElementById("ocean-glass").value'), "10", "old opaque film replaced");
  await evaluate('document.getElementById("ocean-toggle").click(); document.getElementById("ocean-reset").click(); document.getElementById("ocean-close").click()');
  const oceanElapsed = await evaluate('document.getElementById("ocean-scene").contentWindow.__ossState().elapsed');
  await delay(350);
  assert.ok(await evaluate('document.getElementById("ocean-scene").contentWindow.__ossState().elapsed') > oceanElapsed, "ocean waves advance");
  await delay(1300); // Let the first-frame fade finish before making the static fallback.
  await evaluate('document.querySelector("main").style.visibility = "hidden"; document.getElementById("ocean-controls").style.visibility = "hidden"');
  let poster = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  fs.writeFileSync(path.join(root, "web", "ocean", "poster.png"), Buffer.from(poster.data, "base64"));
  await evaluate('document.querySelector("main").style.visibility = ""; document.getElementById("ocean-controls").style.visibility = ""');
  console.log("动态海面已渲染，海浪随时间变化。");
  await delay(300);
  assert.equal(await visibleCount("input"), 1);
  assert.equal(await visibleCount("button"), 2);
  assert.equal(await visibleCount("textarea, select, aside, footer"), 0);
  assert.equal(await evaluate('/[A-Za-z]/.test(document.body.innerText)'), false);
  assert.equal(await evaluate('document.getElementById("result").hidden'), true);
  const glass = await evaluate('getComputedStyle(document.querySelector(".search-field")).backdropFilter');
  assert.ok(glass.includes("blur") && glass.includes("saturate"));
  await screenshot("桌面.png");

  // Freeze the sea so the two render captures differ only in the refracted rim.
  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await delay(250);
  const lensClip = await evaluate('(() => { const r = document.querySelector(".search-field").getBoundingClientRect(); return {x:Math.floor(r.x),y:Math.floor(r.y),width:Math.ceil(r.width),height:Math.ceil(r.height),scale:1}; })()');
  let lens = await command("Page.captureScreenshot", { format: "png", clip: lensClip });
  fs.writeFileSync(path.join(run, "折射开启.png"), Buffer.from(lens.data, "base64"));
  await evaluate('const rimStyle = document.createElement("style"); rimStyle.id = "rim-comparison"; rimStyle.textContent = ".glass::after{-webkit-backdrop-filter:none!important;backdrop-filter:none!important}"; document.head.append(rimStyle);');
  await delay(100);
  lens = await command("Page.captureScreenshot", { format: "png", clip: lensClip });
  fs.writeFileSync(path.join(run, "折射关闭.png"), Buffer.from(lens.data, "base64"));
  await evaluate('document.getElementById("rim-comparison").remove()');
  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });

  await evaluate('document.getElementById("ocean-toggle").click()');
  assert.equal(await visibleCount('input[type="range"]'), 3);
  assert.equal(await evaluate('document.getElementById("ocean-toggle").getAttribute("aria-expanded")'), "true");
  await setRange("ocean-waves", 82);
  await until(async () => (await oceanState()).sea > 1.47, "wave slider changes real renderer");
  await evaluate('document.querySelector("[data-ocean-preset=calm]").click()');
  await until(async () => (await oceanState()).sea < .53, "calm preset changes real waves");
  await evaluate('document.querySelector("[data-ocean-preset=sunset]").click()');
  await until(async () => Math.abs((await oceanState()).daylight - 12) < .01, "sunset preset changes actual sun");
  await screenshot("夕阳.png");
  await setRange("ocean-glass", 30);
  assert.equal(await evaluate('getComputedStyle(document.documentElement).getPropertyValue("--glass-opacity").trim()'), "0.3");
  await evaluate('document.getElementById("ocean-auto").click()');
  await until(async () => !(await oceanState()).manualTimeOfDay, "automatic daylight enabled");
  const autoBefore = (await oceanState()).daylight;
  await delay(700);
  assert.notEqual((await oceanState()).daylight, autoBefore, "automatic daylight advances");
  await setRange("ocean-daylight", 35);
  await until(async () => (await oceanState()).manualTimeOfDay && Math.abs((await oceanState()).daylight - 35) < .01, "manual sun adjustment stops automatic cycle");
  assert.equal(await evaluate('document.getElementById("ocean-auto").getAttribute("aria-pressed")'), "false");
  await evaluate('document.getElementById("ocean-reset").click()');
  await until(async () => Math.abs((await oceanState()).sea - 1.09) < .001 && Math.abs((await oceanState()).daylight - 55) < .001, "restore original plugin defaults");
  await screenshot("海面调节.png");
  assert.equal(await evaluate('/[A-Za-z]/.test(document.body.innerText)'), false);
  await setRange("ocean-waves", 34);
  await setRange("ocean-daylight", 73);
  await setRange("ocean-glass", 28);
  await delay(250);
  await command("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await command("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  assert.equal(await evaluate('document.getElementById("ocean-panel").hidden'), true);

  await evaluate('document.getElementById("search-button").click()');
  assert.equal(await evaluate('document.getElementById("form-error").textContent'), "先输入一个书名。");
  await fillTitle("雨后的借书处");
  await evaluate('document.getElementById("book-title").focus()');
  await command("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
  await command("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  const complete = await newBatch("雨后的借书处", "complete");
  await until(() => evaluate('!document.getElementById("download").hidden && document.querySelector(".book-item")?.dataset.status === "done"'), "result actions visible");
  assert.equal(complete.tasks[0].search_required, true);
  assert.equal(complete.tasks[0].source, "本地演示书库");
  const zip = new Uint8Array(await (await fetch(origin + "api/archive/" + complete.id)).arrayBuffer());
  assert.equal(String.fromCharCode(zip[0], zip[1]), "PK");
  assert.ok(zip.length > 1000);
  assert.equal(await evaluate('/[A-Za-z]/.test(document.body.innerText)'), false);
  await command("Page.reload");
  await until(() => evaluate('document.querySelector(".book-item")?.dataset.status === "done" && !document.getElementById("download").hidden'), "reload keeps download");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "reloaded ocean ready", 60000);
  await until(async () => Math.abs((await oceanState()).sea - .76) < .001 && Math.abs((await oceanState()).daylight - 73) < .001, "saved waves and daylight survive reload");
  assert.equal(await evaluate('getComputedStyle(document.documentElement).getPropertyValue("--glass-opacity").trim()'), "0.28");
  await evaluate('document.getElementById("ocean-toggle").click(); document.getElementById("ocean-reset").click(); document.getElementById("ocean-close").click()');
  await screenshot("结果.png");
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 960, deviceScaleFactor: 1, mobile: true });
  await delay(200);
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  await screenshot("窄屏结果.png");
  await evaluate('document.getElementById("ocean-toggle").click()');
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  await screenshot("窄屏调节.png");
  const fieldPoint = await evaluate('(() => { const r = document.getElementById("book-title").getBoundingClientRect(); return {x:r.x + 20,y:r.y + r.height / 2}; })()');
  await command("Input.dispatchMouseEvent", { type: "mousePressed", x: fieldPoint.x, y: fieldPoint.y, button: "left", clickCount: 1 });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", x: fieldPoint.x, y: fieldPoint.y, button: "left", clickCount: 1 });
  assert.equal(await evaluate('document.getElementById("ocean-panel").hidden'), true, "outside click closes settings");
  await evaluate('document.getElementById("dismiss").click()');
  await fillTitle("");
  assert.equal(await visibleCount("input"), 1);
  assert.equal(await visibleCount("button"), 2);
  assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
  await evaluate('document.getElementById("book-title").blur()');
  await screenshot("窄屏.png");

  if (!appearanceOnly) {
  await fillTitle("雨后的第二间书屋");
  await evaluate('document.getElementById("search-button").click()');
  await newBatch("雨后的第二间书屋", "partial");
  await until(() => evaluate('!document.getElementById("retry").hidden && !!document.querySelector(".error-details")'), "failure and retry");
  await evaluate('document.querySelector(".error-details > summary").click()');
  assert.equal(await evaluate('document.querySelector(".error-details").open'), true);
  config.books.push({ title: "雨后的第二间书屋", url: origin + "demo/gbk.txt", source: "本地演示书库" });
  await api("api/sources", config);
  await evaluate('document.getElementById("retry").click()');
  const retried = await newBatch("雨后的第二间书屋", "complete");
  assert.equal(retried.tasks[0].status, "done");
  await until(() => evaluate('!document.getElementById("download").hidden'), "retried download available");

  const chunk = Buffer.from("海风掀开了书页，这是本机原创的界面功能测试文本。\n".repeat(1400));
  slowServer = http.createServer((request, response) => {
    response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8", "Content-Length": chunk.length * 70 });
    let sent = 0;
    const write = () => { if (++sent <= 70) response.write(chunk); else { clearInterval(timer); response.end(); } };
    const timer = setInterval(write, 180); write();
    response.on("close", () => clearInterval(timer));
  });
  await new Promise(resolve => slowServer.listen(0, "127.0.0.1", resolve));
  config.books.push({ title: "慢慢翻开的书", url: "http://127.0.0.1:" + slowServer.address().port + "/original.txt", source: "本地原创测试" });
  await api("api/sources", config);
  await fillTitle("慢慢翻开的书");
  await evaluate('document.getElementById("search-button").click()');
  await until(async () => (await api("api/state")).batches.find(batch => batch.tasks[0]?.title === "慢慢翻开的书" && batch.tasks[0].bytes > 0), "active download");
  await until(() => evaluate('!document.getElementById("cancel").hidden && document.querySelector(".book-name")?.textContent === "慢慢翻开的书"'), "stop action visible");
  await evaluate('document.getElementById("cancel").click()');
  await newBatch("慢慢翻开的书", "cancelled");
  await until(() => evaluate('!document.getElementById("retry").hidden && !document.getElementById("search-button").disabled'), "stopped task can resume");

  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await delay(200);
  const pausedElapsed = await evaluate('document.getElementById("ocean-scene").contentWindow.__ossState().elapsed');
  await delay(250);
  assert.equal(await evaluate('document.getElementById("ocean-scene").contentWindow.__ossState().elapsed'), pausedElapsed, "reduced motion freezes sea");
  await evaluate('document.getElementById("ocean-toggle").click()');
  await setRange("ocean-daylight", 42);
  await until(async () => Math.abs((await oceanState()).daylight - 42) < .01, "sun can still be adjusted with reduced motion");
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    result: "passed", zip_bytes: zip.length,
    ui_checks: appearanceOnly ? ["glass render and rim comparison", "existing waves and sun retained", "10% default white film", "live ocean controls", "saved settings", "one original book and ZIP", "desktop and narrow screenshots"] : ["compact title search and collapsible ocean settings", "Chinese-only interface", "original Open Sea water and sky",
      "live wave and sunlight sliders", "calm and sunset presets", "glass opacity", "automatic daylight", "saved settings and reset",
      "empty input", "Enter search", "automatic acquisition and ZIP", "reload retains result", "error details and retry",
      "stop active download", "390px layout", "reduced motion"],
    previews: ["预览/桌面.png", "预览/结果.png", "预览/窄屏.png", "预览/窄屏结果.png", "预览/海面调节.png", "预览/夕阳.png", "预览/窄屏调节.png"]
  }));
  socket.close(); socket = null;
  await api("api/shutdown", {}); stopped = true;
  if (serviceError) console.error(serviceError);
} finally {
  if (socket) socket.close();
  if (chrome) chrome.kill();
  if (service && !stopped) service.kill();
  if (slowServer) { slowServer.closeAllConnections(); slowServer.close(); }
}
