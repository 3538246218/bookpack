// Matching UI verification with original local demo text; no external services.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "matching-" + Date.now());
const profile = path.join(run, "chrome-profile");
const preview = path.join(root, "预览");
fs.mkdirSync(run, { recursive: true });
let service, chrome, socket, stopped = false;
let output = "", serviceError = "";
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
  await api("api/sources", { local_dirs: [], wikisource: false, web_sources: [], books:
    ["小镇书信收集所", "小镇书信收藏所", "雨后小镇借书处的最后一封回信"].map(title => ({
      title, author: "原创演示", source: "原创演示书库", url: origin + "demo/original.txt"
    }))
  });
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
  const requests = new Map(), errors = [];
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
  async function screenshot(name) {
    await delay(800);
    const shot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    fs.writeFileSync(path.join(preview, name), Buffer.from(shot.data, "base64"));
  }
  async function submit(title) {
    await evaluate(`document.getElementById("book-title").value = ${JSON.stringify(title)}; document.getElementById("search-button").click()`);
  }
  await command("Runtime.enable");
  await command("Page.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: origin });
  await until(() => evaluate('document.readyState === "complete" && !document.getElementById("search-button").disabled'), "UI ready");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "background ready", 60000);
  await submit("小镇书信收寄所");
  const pending = await until(async () => (await api("api/state")).batches.find(batch => batch.status === "needs_choice"), "low-score candidates");
  await until(() => evaluate('document.querySelectorAll("button[data-candidate-id]").length === 2 && !document.querySelector("button[data-candidate-id]").disabled'), "candidate choices rendered");
  assert.equal(await evaluate('document.querySelectorAll(".candidate-list strong").length'), 2);
  assert.equal(await evaluate('document.getElementById("retry").hidden'), true);
  assert.equal(await evaluate('document.getElementById("result-status").textContent'), "请选择要获取的书籍");
  await screenshot("候选书籍.png");
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
  await screenshot("候选书籍窄屏.png");
  const candidateId = pending.tasks[0].candidates[0].candidate_id;
  await command("Page.reload", { ignoreCache: true });
  await until(() => evaluate('document.readyState === "complete" && !!document.querySelector("button[data-candidate-id]")'), "candidate list retained on reload");
  assert.equal(await evaluate('document.querySelector("button[data-candidate-id]").dataset.candidateId'), candidateId);
  await evaluate('document.querySelector("button[data-candidate-id]").click()');
  const completed = await until(async () => (await api("api/state")).batches.find(batch => batch.id === pending.id && batch.status === "complete"), "chosen candidate acquired");
  await until(() => evaluate('!document.getElementById("download").hidden'), "download action rendered");
  assert.equal(completed.tasks[0].matched_title, "小镇书信收集所");
  assert.equal(completed.tasks[0].match_confidence, "selected");
  const zip = Buffer.from(await (await fetch(origin + "api/archive/" + pending.id)).arrayBuffer());
  assert.ok(zip.length > 100 && zip.subarray(0, 2).toString() === "PK");
  await screenshot("候选获取结果.png");
  await evaluate('document.getElementById("dismiss").click()');
  await submit("雨后小镇借书处的最后一封回音");
  const automatic = await until(async () => (await api("api/state")).batches.find(batch => batch.tasks[0]?.title === "雨后小镇借书处的最后一封回音" && batch.status === "complete"), "high-score automatic match");
  assert.equal(automatic.tasks[0].match_confidence, "high");
  await until(() => evaluate('document.querySelector(".book-name")?.textContent === "雨后小镇借书处的最后一封回信" && document.querySelector(".book-meta").textContent.includes("相似度")'), "automatic correction shown");
  await screenshot("自动匹配.png");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", zip_bytes: zip.length,
    checks: ["clickable low-confidence choices", "candidate reload", "canonical title and ZIP", "automatic high-score match", "390px layout", "no script exceptions"],
    previews: ["候选书籍.png", "候选书籍窄屏.png", "候选获取结果.png", "自动匹配.png"] }));
  socket.close(); socket = null;
  await api("api/shutdown", {}); stopped = true;
  if (serviceError) console.error(serviceError);
} finally {
  if (socket) socket.close();
  if (chrome) chrome.kill();
  if (service && !stopped) service.kill();
}
