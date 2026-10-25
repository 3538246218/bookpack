// Focused input-drawer verification with original local text and a local search fixture.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "suggest-" + Date.now());
const profile = path.join(run, "chrome-profile");
const preview = path.join(root, "预览");
fs.mkdirSync(run, { recursive: true });
let service, chrome, socket, fixture, stopped = false;
let output = "", serviceError = "";
const queries = [], errors = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await callback();
    if (value) return value;
    await delay(80);
  }
  throw new Error("Timed out: " + label);
}
try {
  fixture = http.createServer(async (request, response) => {
    const query = new URL(request.url, "http://localhost").searchParams.get("q") || "";
    queries.push(query);
    await delay(query === "山城" ? 1100 : query === "雨后" ? 700 : 80);
    const title = query === "山城" ? "山城的最后一盏灯" : query === "海边" ? "海边书信集" : query === "雨后" ? "雨后旧书收集处" : "";
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(title ? `<a href="/book">${title}</a>` : "<p>没有结果</p>");
  });
  await new Promise(resolve => fixture.listen(0, "127.0.0.1", resolve));
  const fixtureOrigin = "http://127.0.0.1:" + fixture.address().port;
  service = spawn("C:\\Users\\PC\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
    ["app.py", "--home", run, "--port", "0"], { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  service.stdout.on("data", data => { output += data.toString("utf8"); });
  service.stderr.on("data", data => { serviceError += data.toString("utf8"); });
  const origin = await until(() => output.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0], "server ready");
  async function api(route, data) {
    const response = await fetch(origin + route, data === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
    });
    const result = await response.json();
    assert.ok(response.ok, JSON.stringify(result));
    return result;
  }
  await api("api/sources", { local_dirs: [], wikisource: false, books: [
    { title: "雨后小镇借书处", author: "原创甲" }, { title: "雨后小镇借书处", author: "原创乙" },
    { title: "雨后小镇来信处", author: "原创演示" }, { title: "海边的第七封来信", author: "原创演示" }
  ].map(row => ({ ...row, source: "原创演示书库", url: origin + "demo/original.txt" })),
  web_sources: [{ id: "fixture", name: "在线联想演示", url: fixtureOrigin, search: { url: "/search", params: { q: "{title}" } } }] });
  chrome = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", ["--headless=new",
    "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
    "--user-data-dir=" + profile, "--window-size=1440,1100", "about:blank"], { windowsHide: true, stdio: "ignore" });
  const activePort = path.join(profile, "DevToolsActivePort");
  const port = await until(() => fs.existsSync(activePort) && fs.readFileSync(activePort, "utf8").split("\n")[0], "browser ready");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
  socket = new WebSocket(targets.find(item => item.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let counter = 0;
  const requests = new Map();
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (requests.has(message.id)) {
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
  async function fill(value) {
    await evaluate(`document.getElementById("book-title").focus(); document.getElementById("book-title").value = ${JSON.stringify(value)}; document.getElementById("book-title").dispatchEvent(new Event("input", { bubbles: true }));`);
  }
  async function key(name) {
    const codes = { ArrowDown: 40, ArrowUp: 38, Enter: 13, Escape: 27, Tab: 9 };
    for (const type of ["keyDown", "keyUp"]) await command("Input.dispatchKeyEvent", {
      type, key: name, code: name, windowsVirtualKeyCode: codes[name],
      ...(name === "Enter" && type === "keyDown" ? { text: "\r", unmodifiedText: "\r" } : {})
    });
  }
  const titles = () => evaluate('[...document.querySelectorAll(".suggestion-copy strong")].map(node => node.textContent)');
  async function screenshot(name) {
    await delay(400);
    const shot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    fs.writeFileSync(path.join(preview, name), Buffer.from(shot.data, "base64"));
  }
  await command("Runtime.enable");
  await command("Page.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: origin });
  await until(() => evaluate('document.readyState === "complete" && !document.getElementById("search-button").disabled'), "UI ready");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "background ready", 60000);
  await fill("雨后");
  await until(async () => (await titles()).includes("雨后小镇借书处"), "local completion before remote result");
  assert.equal(await evaluate('document.getElementById("book-title").value'), "雨后");
  assert.equal((await api("api/state")).batches.length, 0);
  await until(async () => (await titles()).includes("雨后旧书收集处"), "online completion appended");
  await screenshot("搜索补全.png");
  await key("Escape");
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").hidden'), true);
  await fill("山城");
  await until(() => queries.includes("山城"), "old query in flight");
  await fill("海边");
  await until(async () => (await titles()).includes("海边书信集"), "new query takes priority");
  await delay(1200);
  assert.equal((await titles()).includes("山城的最后一盏灯"), false);
  await fill("");
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").hidden'), true);
  const beforeComposition = queries.length;
  await evaluate('document.getElementById("book-title").dispatchEvent(new CompositionEvent("compositionstart")); document.getElementById("book-title").value = "雨后小"; document.getElementById("book-title").dispatchEvent(new InputEvent("input", {bubbles:true,isComposing:true}));');
  await key("Enter");
  await delay(500);
  assert.equal(queries.length, beforeComposition);
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").hidden'), true);
  assert.equal((await api("api/state")).batches.length, 0);
  await evaluate('document.getElementById("book-title").dispatchEvent(new CompositionEvent("compositionend"))');
  await until(async () => (await titles()).includes("雨后小镇借书处"), "composition end starts completion");
  await fill("雨后小镇借书所");
  await until(() => evaluate('!document.getElementById("suggestion-drawer").hidden && document.querySelector(".suggestion-copy strong")?.textContent === "雨后小镇借书处"'), "typo correction in input drawer");
  await screenshot("搜索纠错.png");
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
  await screenshot("搜索抽屉窄屏.png");
  const point = await evaluate('(() => { const r=document.querySelector(".suggestion-option").getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()');
  await command("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
  assert.equal(await evaluate('document.getElementById("book-title").value'), "雨后小镇借书处");
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").hidden'), true);
  assert.equal((await api("api/state")).batches.length, 0);
  await fill("雨后小");
  await until(async () => (await titles()).length >= 3 && await evaluate('!document.getElementById("suggestion-drawer").hidden'), "keyboard choices");
  await key("ArrowDown"); await key("ArrowDown");
  assert.ok(await evaluate('document.getElementById("book-title").getAttribute("aria-activedescendant")'));
  await key("Enter");
  assert.equal(await evaluate('document.getElementById("book-title").value'), "雨后小镇借书处");
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").hidden'), true);
  assert.equal((await api("api/state")).batches.length, 0);
  await key("Enter");
  const batch = await until(async () => (await api("api/state")).batches.find(row => row.status === "complete"), "selected title searched and packaged");
  assert.equal(batch.tasks[0].matched_author, "原创乙");
  const zip = Buffer.from(await (await fetch(origin + "api/archive/" + batch.id)).arrayBuffer());
  assert.ok(zip.length > 100 && zip.subarray(0, 2).toString() === "PK");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", zip_bytes: zip.length,
    checks: ["automatic prefix drawer", "typo correction", "local then online titles", "stale result suppression", "IME composition", "click to fill only", "keyboard selection", "selected author used on search", "390px layout", "original ZIP"],
    previews: ["搜索补全.png", "搜索纠错.png", "搜索抽屉窄屏.png"] }));
  socket.close(); socket = null;
  await api("api/shutdown", {}); stopped = true;
  if (serviceError) console.error(serviceError);
} finally {
  if (socket) socket.close();
  if (chrome) chrome.kill();
  if (service && !stopped) service.kill();
  if (fixture) { fixture.closeAllConnections(); fixture.close(); }
}
