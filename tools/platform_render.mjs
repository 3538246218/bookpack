// Verify real platform-catalog suggestions in an isolated local app instance.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const run = path.join(root, ".ui-run", "platform-" + Date.now());
const profile = path.join(run, "chrome-profile");
fs.mkdirSync(run, { recursive: true });
let service, chrome, socket, origin = "", output = "", stopped = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await callback(); if (value) return value;
    await delay(100);
  }
  throw new Error("Timed out: " + label);
}
async function api(route, data) {
  const response = await fetch(origin + route, data === undefined ? {} : {
    method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)
  });
  const result = await response.json();
  assert.ok(response.ok, JSON.stringify(result)); return result;
}
try {
  service = spawn("C:\\Users\\PC\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
    ["app.py","--home",run,"--port","0"], {cwd:root,windowsHide:true,stdio:["ignore","pipe","pipe"]});
  service.stdout.on("data", data => { output += data.toString("utf8"); });
  origin = await until(() => output.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0], "app ready");
  await api("api/sources",{local_dirs:[],wikisource:false,web_sources:[],books:[{
    title:"尚未收录的原创平台样文",author:"原创作者",source:"起点下载测试站",url:origin+"demo/original.txt"
  }]});
  chrome = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",["--headless=new",
    "--enable-unsafe-swiftshader","--no-first-run","--no-default-browser-check","--remote-debugging-port=0",
    "--user-data-dir="+profile,"--window-size=1440,1100","about:blank"],{windowsHide:true,stdio:"ignore"});
  const activePort = path.join(profile,"DevToolsActivePort");
  const port = await until(() => fs.existsSync(activePort)&&fs.readFileSync(activePort,"utf8").split("\n")[0],"browser ready");
  const targets = await (await fetch("http://127.0.0.1:"+port+"/json")).json();
  socket = new WebSocket(targets.find(row=>row.type==="page").webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  let counter=0; const requests=new Map(), errors=[];
  socket.addEventListener("message",event=>{
    const message=JSON.parse(event.data);
    if(requests.has(message.id)){const request=requests.get(message.id);requests.delete(message.id);message.error?request.reject(new Error(message.error.message)):request.resolve(message.result);}
    if(message.method==="Runtime.exceptionThrown")errors.push(message.params.exceptionDetails.text);
  });
  const command=(method,params={})=>new Promise((resolve,reject)=>{const id=++counter;requests.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  async function evaluate(expression){const result=await command("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result?.value;}
  async function fill(query){
    await evaluate(`document.getElementById("book-title").focus();document.getElementById("book-title").value=${JSON.stringify(query)};document.getElementById("book-title").dispatchEvent(new Event("input",{bubbles:true}));`);
    await until(()=>evaluate('!document.getElementById("suggestion-drawer").hidden && document.querySelector(".suggestion-option")'),"suggestions ready");
  }
  async function shot(name){await delay(350);const result=await command("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});fs.writeFileSync(path.join(root,"预览",name),Buffer.from(result.data,"base64"));}
  await command("Runtime.enable");await command("Page.enable");
  await command("Emulation.setDeviceMetricsOverride",{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await command("Page.navigate",{url:origin});
  await until(()=>evaluate('document.readyState==="complete"&&!document.getElementById("book-title").disabled'),"UI ready");
  await until(()=>evaluate('document.getElementById("ocean-scene").contentDocument?.body.classList.contains("ready")'),"ocean ready",60000);
  await fill("神秘");
  assert.equal(await evaluate('document.querySelector(".suggestion-option .work-platform").textContent'),"起点");
  assert.ok(await evaluate('document.querySelector(".suggestion-option").textContent.includes("佛前献花")'));
  await shot("平台归属.png");
  await fill("我不是戏神");
  assert.equal(await evaluate('document.querySelector(".suggestion-option .work-platform").textContent'),"番茄");
  await shot("番茄平台.png");
  await fill("尚未收录");
  assert.equal(await evaluate('document.querySelector(".suggestion-option .work-platform").textContent'),"平台未核实");
  assert.equal(await evaluate('document.getElementById("suggestion-list").innerText.includes("下载测试站")'),false);
  const qidian=(await api("api/suggest",{query:"神秘复苏",online:false})).suggestions[0];
  const rendered=await evaluate(`taskMarkup({...${JSON.stringify(qidian)},id:"preview",matched_title:${JSON.stringify(qidian.title)},matched_author:${JSON.stringify(qidian.author)},status:"searching",source:"错误的下载站"})`);
  assert.ok(rendered.includes("起点")&&!rendered.includes("错误的下载站"));
  await command("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await fill("神秘");
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await evaluate('document.getElementById("suggestion-scroll").scrollTop=150');
  assert.equal(await evaluate('document.getElementById("suggestion-drawer").scrollTop'),0);
  await shot("平台归属窄屏.png");
  assert.equal((await api("api/state")).batches.length,0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:"passed",checks:["actual Qidian and Fanqie metadata","unknown remains unknown","mirror label hidden","result platform independent of download source","fixed drawer frame and narrow layout"],previews:["平台归属.png","番茄平台.png","平台归属窄屏.png"]}));
  await api("api/shutdown",{});stopped=true;
} finally {
  socket?.close();chrome?.kill();if(service&&!stopped)service.kill();
}
