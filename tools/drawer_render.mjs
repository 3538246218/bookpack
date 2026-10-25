// Capture only drawer appearance and scrolling, using fixed sample titles.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const before = process.argv.includes("--before");
const run = path.join(root, ".ui-run", "drawer-render-" + Date.now());
const profile = path.join(run, "chrome-profile");
fs.mkdirSync(run, { recursive: true });
const origin = JSON.parse(fs.readFileSync(path.join(root, "运行地址.json"), "utf8")).url;
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
let chrome, socket;
try {
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
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result?.value;
  }
  await command("Runtime.enable"); await command("Page.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.addScriptToEvaluateOnNewDocument", { source: `
    const nativeFetch = window.fetch.bind(window);
    const titles = ["雨后小镇借书处", "雨后小镇来信处", "雨后小镇的第七封信", "雨后旧书收藏处", "雨后去海边的人", "雨后小镇的最后一盏灯", "雨后小镇失物招领处", "雨后的第十二条街"];
    window.fetch = (url, options) => {
      if (url === "/api/state") return Promise.resolve(new Response(JSON.stringify({batches:[]})));
      if (url === "/api/suggest") {
        const query = JSON.parse(options.body).query;
        const suggestions = (query.length > 3 ? titles.slice(0, 2) : titles).map(title => ({title, author:"原创演示", source:"渲染样本书源", suggestion_kind:query.length>3?"fuzzy":"prefix"}));
        return Promise.resolve(new Response(JSON.stringify({suggestions,online_pending:false})));
      }
      return nativeFetch(url, options);
    };` });
  await command("Page.navigate", { url: origin });
  await until(() => evaluate('document.readyState === "complete" && !document.getElementById("book-title").disabled'), "page ready");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "ocean ready", 60000);
  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  async function fill(query) {
    await evaluate(`document.getElementById("book-title").focus(); document.getElementById("book-title").value=${JSON.stringify(query)};document.getElementById("book-title").dispatchEvent(new Event("input",{bubbles:true}));`);
    await until(() => evaluate('!document.getElementById("suggestion-drawer").hidden && document.querySelector(".suggestion-option")'), "drawer ready");
  }
  async function shot(name) {
    await delay(150);
    const result = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const destination = path.join(before ? run : path.join(root, "预览"), name);
    fs.writeFileSync(destination, Buffer.from(result.data, "base64"));
    return destination;
  }
  const files = [];
  await fill("雨后");
  files.push(await shot(before ? "滚动前.png" : "搜索补全.png"));
  await evaluate('(document.getElementById("suggestion-scroll") || document.getElementById("suggestion-drawer")).scrollTop=170');
  files.push(await shot(before ? "滚动后.png" : "抽屉滚动.png"));
  const geometry = await evaluate('(() => {const frame=document.getElementById("suggestion-drawer"), inner=document.getElementById("suggestion-scroll")||frame;return {frameScroll:frame.scrollTop,innerScroll:inner.scrollTop,frameHeight:frame.clientHeight,innerHeight:inner.clientHeight,pageScroll:scrollY,visibleBadges:document.querySelectorAll(".suggestion-kind").length,sourceVisible:frame.innerText.includes("渲染样本书源")}})()');
  if (!before && (geometry.frameScroll !== 0 || geometry.innerScroll <= 0 || geometry.visibleBadges || geometry.sourceVisible)) throw new Error("Drawer geometry or removed labels incorrect: " + JSON.stringify(geometry));
  await fill("雨后小镇借书所");
  files.push(await shot(before ? "纠错.png" : "搜索纠错.png"));
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await fill("雨后");
  await evaluate('(document.getElementById("suggestion-scroll") || document.getElementById("suggestion-drawer")).scrollTop=170');
  files.push(await shot(before ? "窄屏滚动.png" : "搜索抽屉窄屏.png"));
  if (!before) {
    const inputPoint = await evaluate('(() => {const r=document.getElementById("book-title").getBoundingClientRect();return {x:r.x+30,y:r.y+r.height/2};})()');
    await command("Input.dispatchMouseEvent", {type:"mousePressed",button:"left",clickCount:1,...inputPoint});
    await command("Input.dispatchMouseEvent", {type:"mouseReleased",button:"left",clickCount:1,...inputPoint});
    await delay(450);
    for (let index = 0; index < 8; index++) {
      await command("Input.dispatchKeyEvent", {type:"keyDown",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
      await command("Input.dispatchKeyEvent", {type:"keyUp",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
    }
    const keyboard = await evaluate('(() => {const frame=document.getElementById("suggestion-drawer"),inner=document.getElementById("suggestion-scroll"),option=document.querySelector(".suggestion-option[aria-selected=true]"); const a=inner.getBoundingClientRect(),b=option?.getBoundingClientRect();return {frameScroll:frame.scrollTop,inside:!!b&&b.top>=a.top&&b.bottom<=a.bottom+1,pageScroll:scrollY,focused:document.activeElement.id,errors:!!option};})()');
    if (keyboard.frameScroll !== 0 || !keyboard.inside || keyboard.pageScroll !== 0) throw new Error("Keyboard scrolling moved the frame: " + JSON.stringify(keyboard));
  }
  if (errors.length) throw new Error(errors.join("; "));
  console.log(JSON.stringify({before,geometry,files}));
} finally {
  socket?.close(); chrome?.kill();
}
