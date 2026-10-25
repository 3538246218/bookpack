// Focused checks for drawer clearance, sky composition and changing an automatic match.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "refinement-" + Date.now());
const profile = path.join(run, "chrome-profile");
fs.mkdirSync(run, { recursive: true });
const originalTitle = "雨后小镇借书处的最后一封回信";
const typo = "雨后小镇借书处的最后一封回音";
const otherTitle = "雨后小镇借书处的另一封信";
const source = http.createServer((request, response) => {
  const chunk = Buffer.from("原创界面样文：海边的旧邮局重新开门了。\n".repeat(2048));
  const slow = request.url === "/slow.txt";
  response.writeHead(200, {"Content-Type":"text/plain; charset=utf-8", "Content-Length":chunk.length * (slow ? 50 : 1)});
  if (!slow) { response.end(chunk); return; }
  let sent = 0;
  const timer = setInterval(() => { response.write(chunk); if (++sent === 50) { clearInterval(timer); response.end(); } }, 120);
  response.on("close", () => clearInterval(timer));
});
await new Promise(resolve => source.listen(0, "127.0.0.1", resolve));
const sourceUrl = "http://127.0.0.1:" + source.address().port;
let service, chrome, socket, origin = "", output = "", stopped = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await callback(); if (value) return value; await delay(80); }
  throw new Error("Timed out: " + label);
}
async function api(route, data) {
  const response = await fetch(origin + route, data === undefined ? {} : {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)
  });
  const result = await response.json(); assert.ok(response.ok, JSON.stringify(result)); return result;
}
try {
  service = spawn("C:\\Users\\PC\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
    ["app.py", "--home", run, "--port", "0"], {cwd:root, windowsHide:true, stdio:["ignore","pipe","pipe"]});
  service.stdout.on("data", data => { output += data.toString("utf8"); });
  origin = await until(() => output.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0], "app ready");
  await api("api/sources", {local_dirs:[], wikisource:false, web_sources:[], books:[
    {title:originalTitle, author:"原创演示", url:sourceUrl + "/slow.txt"},
    {title:otherTitle, author:"原创演示", url:sourceUrl + "/other.txt"}
  ]});
  chrome = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", ["--headless=new",
    "--enable-unsafe-swiftshader", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
    "--user-data-dir=" + profile, "--window-size=1440,1100", "about:blank"], {windowsHide:true, stdio:"ignore"});
  const activePort = path.join(profile, "DevToolsActivePort");
  const port = await until(() => {
    try { return fs.readFileSync(activePort,"utf8").split("\n")[0]; }
    catch (error) { if (["ENOENT", "EBUSY", "EPERM"].includes(error.code)) return false; throw error; }
  }, "browser ready");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
  socket = new WebSocket(targets.find(row => row.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve,reject) => { socket.addEventListener("open",resolve,{once:true}); socket.addEventListener("error",reject,{once:true}); });
  let counter = 0; const requests = new Map(), errors = [];
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (requests.has(message.id)) { const request = requests.get(message.id); requests.delete(message.id); message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result); }
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  });
  const command = (method,params={}) => new Promise((resolve,reject) => { const id = ++counter; requests.set(id,{resolve,reject}); socket.send(JSON.stringify({id,method,params})); });
  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result?.value;
  }
  async function shot(name) {
    await delay(200);
    const result = await command("Page.captureScreenshot", {format:"png", captureBeyondViewport:false});
    fs.writeFileSync(path.join(root,"预览",name), Buffer.from(result.data,"base64"));
  }
  async function fill(query) {
    const point = await evaluate('(() => { const r=document.getElementById("book-title").getBoundingClientRect(); return {x:r.x+25,y:r.y+r.height/2}; })()');
    await command("Input.dispatchMouseEvent", {type:"mousePressed",button:"left",clickCount:1,...point});
    await command("Input.dispatchMouseEvent", {type:"mouseReleased",button:"left",clickCount:1,...point});
    await evaluate(`document.getElementById("book-title").focus({preventScroll:true}); document.getElementById("book-title").value=${JSON.stringify(query)}; document.getElementById("book-title").dispatchEvent(new Event("input",{bubbles:true}));`);
    await until(() => evaluate('!document.getElementById("suggestion-drawer").hidden && document.querySelector(".suggestion-option")'), "drawer ready");
  }
  await command("Runtime.enable"); await command("Page.enable");
  await command("Emulation.setEmulatedMedia", {features:[{name:"prefers-reduced-motion", value:"reduce"}]});
  await command("Emulation.setDeviceMetricsOverride", {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await command("Page.navigate", {url:origin});
  await until(() => evaluate('document.readyState === "complete" && !document.getElementById("book-title").disabled'), "UI ready");
  await until(() => evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'), "ocean ready", 60000);
  await evaluate('document.getElementById("ocean-toggle").click(); document.querySelector("[data-ocean-preset=sunset]").click(); document.getElementById("ocean-close").click();');
  await delay(250);
  await shot("天空搜索.png");
  await command("Emulation.setEmulatedMedia", {features:[{name:"prefers-reduced-motion", value:"no-preference"}]});
  const geometries = [];
  for (const [width,height,name] of [[1440,1000,"天空抽屉.png"],[390,844,"天空抽屉窄屏.png"],[844,390,"横屏抽屉.png"],[568,320,"紧凑横屏抽屉.png"]]) {
    await command("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile:width<1000});
    await fill("神秘");
    const geometry = await evaluate(`(() => {
      const drawer = document.getElementById("suggestion-drawer"), launcher = document.getElementById("ocean-toggle"), input = document.getElementById("search-composer");
      const d = drawer.getBoundingClientRect(), l = launcher.getBoundingClientRect(), i = input.getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,drawerBottom:d.bottom,drawerTop:d.top,launcherTop:l.top,inputTop:i.top,inputBottom:i.bottom,placement:drawer.dataset.placement,overflow:document.documentElement.scrollWidth>innerWidth};
    })()`);
    assert.ok(geometry.drawerBottom <= geometry.launcherTop - 10, JSON.stringify(geometry));
    assert.ok(geometry.drawerTop >= 10 && !geometry.overflow, JSON.stringify(geometry));
    assert.ok(geometry.inputBottom <= height * .3 - 19, JSON.stringify(geometry));
    const count = await evaluate('document.querySelectorAll(".suggestion-option").length');
    for (let i = 0; i < count; i++) {
      await command("Input.dispatchKeyEvent", {type:"keyDown",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
      await command("Input.dispatchKeyEvent", {type:"keyUp",key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40});
    }
    assert.ok(await evaluate(`(() => { const row=document.querySelector('.suggestion-option[aria-selected="true"]').getBoundingClientRect(), box=document.getElementById("suggestion-scroll").getBoundingClientRect();return row.top>=box.top && row.bottom<=box.bottom+1 && document.getElementById("suggestion-drawer").scrollTop===0; })()`));
    geometries.push(geometry);
    await shot(name);
  }
  await command("Emulation.setDeviceMetricsOverride", {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`document.getElementById("book-title").value=${JSON.stringify(typo)}; bookSuggestions.close(); document.getElementById("search-button").click();`);
  await until(async () => (await api("api/state")).batches[0]?.tasks[0]?.status === "downloading", "automatic download started");
  await until(() => evaluate('!!document.querySelector(".alternative-details") && !!document.querySelector("button[data-current=false]:not(:disabled)")'), "active alternatives available");
  await evaluate('document.querySelector(".alternative-details > summary").click()');
  assert.equal(await evaluate('document.querySelector(".alternative-details").open'), true);
  await shot("自动匹配改选.png");
  await evaluate('document.querySelector(".alternative-details button[data-current=false]").click()');
  const changed = await until(async () => { const batch=(await api("api/state")).batches[0]; return batch?.status === "complete" && batch.tasks[0].matched_title === otherTitle && batch; }, "replacement acquired and packaged");
  assert.equal(changed.tasks[0].match_confidence, "selected");
  assert.equal(changed.tasks[0].candidates.length, 2);
  await until(() => evaluate('!document.getElementById("download").hidden && document.querySelector(".alternative-details").open'), "expanded alternatives retained after polling");
  await evaluate('document.getElementById("ocean-glass").value=32;document.getElementById("ocean-glass").dispatchEvent(new Event("input",{bubbles:true}));');
  const styles = await until(async () => {
    const current = await evaluate(`(() => {
    return {surfaces:[".search-field",".search-button",".suggestion-drawer",".ocean-panel",".result",".candidate-option"].map(selector=>({selector,color:getComputedStyle(document.querySelector(selector)).backgroundColor})),small:parseFloat(getComputedStyle(document.querySelector(".setting-extremes")).fontSize),unknown:parseFloat(getComputedStyle(document.querySelector(".work-platform.is-unknown")).fontSize),weight:getComputedStyle(document.getElementById("search-button")).fontWeight};
    })()`);
    return current.surfaces.every(surface => surface.color.endsWith(surface.selector === ".search-button" ? "0.41)" : "0.32)")) && current;
  }, "glass transitions settled", 6000);
  for (const surface of styles.surfaces) assert.ok(surface.color.endsWith(surface.selector === ".search-button" ? "0.41)" : "0.32)"), JSON.stringify(styles));
  assert.ok(styles.small >= 10 && styles.unknown >= 10 && Number(styles.weight) >= 700, JSON.stringify(styles));
  await evaluate('document.getElementById("ocean-glass").value=10;document.getElementById("ocean-glass").dispatchEvent(new Event("input",{bubbles:true}));');
  await shot("改选完成.png");
  const archive = Buffer.from(await (await fetch(origin + "api/archive/" + changed.id)).arrayBuffer());
  assert.ok(archive.length > 100 && archive.subarray(0,2).toString() === "PK");
  await command("Page.reload", {ignoreCache:true});
  await until(() => evaluate(`document.querySelector(".book-name")?.textContent === ${JSON.stringify(otherTitle)} && !!document.querySelector(".alternative-details")`), "selection survives refresh");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({result:"passed", geometries, styles, selected:changed.tasks[0].matched_title, zip_bytes:archive.length}));
  await api("api/shutdown", {}); stopped = true;
} finally {
  socket?.close(); chrome?.kill(); if (service && !stopped) service.kill(); source.closeAllConnections(); source.close();
}
