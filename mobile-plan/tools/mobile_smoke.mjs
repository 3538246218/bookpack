// Delivery checks for the mobile prototype; no real novel fetches or desktop state.
import fs from "node:fs";
import path from "node:path";
import {spawn} from "node:child_process";
import {fileURLToPath,pathToFileURL} from "node:url";
import assert from "node:assert/strict";

const plan=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root=path.dirname(plan), run=path.join(root,".ui-run","mobile-"+Date.now());
const previews=path.join(plan,"previews"), downloads=path.join(run,"downloads"),profile=path.join(run,"chrome-profile");
for (const directory of [run,previews,downloads]) fs.mkdirSync(directory,{recursive:true});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label,timeout=15000) {const end=Date.now()+timeout;while(Date.now()<end){const value=await fn();if(value)return value;await delay(80);}throw Error("Timed out: "+label);}
let service,chrome,socket,output="";
try {
  service=spawn("python",["-X","utf8",path.join(plan,"tools/serve.py"),"--port","0"],{cwd:plan,windowsHide:true,stdio:["ignore","pipe","ignore"]});
  service.stdout.on("data",bytes=>output+=bytes.toString("utf8"));
  const origin=await until(()=>output.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0],"preview ready");
  chrome=spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",["--headless=new","--no-first-run","--no-default-browser-check","--remote-debugging-port=0","--user-data-dir="+profile,"about:blank"],{windowsHide:true,stdio:"ignore"});
  const port=await until(()=>fs.existsSync(path.join(profile,"DevToolsActivePort"))&&fs.readFileSync(path.join(profile,"DevToolsActivePort"),"utf8").split("\n")[0],"Chrome ready");
  const targets=await(await fetch("http://127.0.0.1:"+port+"/json")).json();
  socket=new WebSocket(targets.find(target=>target.type==="page").webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  let counter=0;const pending=new Map(),errors=[];
  socket.addEventListener("message",event=>{const message=JSON.parse(event.data);if(pending.has(message.id)){const request=pending.get(message.id);pending.delete(message.id);message.error?request.reject(Error(message.error.message)):request.resolve(message.result);}if(message.method==="Runtime.exceptionThrown")errors.push(message.params.exceptionDetails.exception?.description||message.params.exceptionDetails.text);});
  const cmd=(method,params={})=>new Promise((resolve,reject)=>{const id=++counter;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  async function evaluate(expression){const r=await cmd("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const nav=async name=>{await click(`.bottom-nav [data-nav="${name}"]`);await until(()=>evaluate(`!document.querySelector('[data-page="${name}"]').hidden`),"page "+name);};
  const closed=()=>until(()=>evaluate('!document.getElementById("bottom-sheet").open'),"sheet closed");
  const viewport=(width,height)=>cmd("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:width<=980});
  async function screenshot(name){await delay(550);const r=await cmd("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});fs.writeFileSync(path.join(previews,name),Buffer.from(r.data,"base64"));}
  async function layout(label){assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),label+" no horizontal overflow");assert.ok(await evaluate('(()=>{const r=document.querySelector(".bottom-nav").getBoundingClientRect();return r.bottom<=innerHeight+1&&r.top>=0})()'),label+" navigation visible");assert.ok(await evaluate('Array.from(document.querySelectorAll(".bottom-nav button")).every(b=>b.getBoundingClientRect().height>=44&&b.getBoundingClientRect().width>=44)'),label+" touch targets");}
  await cmd("Runtime.enable");await cmd("Page.enable");await cmd("Browser.setDownloadBehavior",{behavior:"allow",downloadPath:downloads});await viewport(390,844);await cmd("Emulation.setTouchEmulationEnabled",{enabled:true,maxTouchPoints:1});
  await cmd("Page.navigate",{url:origin});await until(()=>evaluate('document.readyState==="complete"&&document.querySelectorAll("#inspiration-list .book-row").length===2'),"home ready");
  await layout("390x844");await screenshot("找书.png");
  const tap=await evaluate('(()=>{const r=document.querySelector(".bottom-nav [data-nav=radar]").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
  await cmd("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[tap]});
  assert.ok(await evaluate('!!document.querySelector(".tap-ripple")'));
  await cmd("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]});
  await until(()=>evaluate('!document.querySelector("[data-page=radar]").hidden'),"touch navigation");
  assert.equal(await evaluate('document.querySelectorAll(".radar-book").length'),20);
  assert.match(await evaluate('document.getElementById("radar-summary").textContent'),/190/);
  assert.match(await evaluate('document.getElementById("snapshot-date").textContent'),/2026-10-13/);
  await screenshot("雷达.png");
  await evaluate('window.__firstMobileCard=document.querySelector(".radar-book")');await click("#radar-more");
  assert.equal(await evaluate('document.querySelectorAll(".radar-book").length'),40);
  assert.equal(await evaluate('window.__firstMobileCard===document.querySelector(".radar-book")'),true);
  for(const channel of ["female","male"]){await click(`[data-channel="${channel}"]`);for(const rank of ["new","read"]){await click(`[data-rank="${rank}"]`);const total=channel==="female"?180:190;assert.ok(await evaluate(`document.getElementById('radar-summary').textContent.includes('${total}')`));}}
  await click("#open-filters");assert.equal(await evaluate('getComputedStyle(document.getElementById("bottom-sheet")).animationDuration'),"0.5s");
  await evaluate('document.getElementById("filter-category").value="传统玄幻";document.getElementById("filter-status").value="serial";document.getElementById("filter-min").value="500000";document.getElementById("filter-sort").value="pos"');
  await screenshot("底部筛选.png");await click("#apply-filters");await closed();
  assert.ok(await evaluate('document.querySelectorAll(".radar-book").length>0&&Array.from(document.querySelectorAll(".radar-copy p")).every(p=>p.textContent.includes("传统玄幻"))'));
  assert.equal(await evaluate('document.getElementById("filter-count").textContent'),"4");
  await evaluate('document.getElementById("radar-query").value="完全没有匹配的演示标题";document.getElementById("radar-query").dispatchEvent(new Event("input",{bubbles:true}))');
  assert.equal(await evaluate('document.querySelectorAll(".radar-book").length'),0);await click("[data-reset-filters]");
  await evaluate('document.getElementById("radar-query").value="神通者";document.getElementById("radar-query").dispatchEvent(new Event("input",{bubbles:true}))');
  const selected=await evaluate('(()=>{const b=window.MOBILE_PREVIEW_DATA.boards["male/read"].items.find(b=>b.title==="神通者");return{title:b.title,author:b.author,id:b.book_id}})()');
  await click("#radar-list [data-pick]");await until(()=>evaluate('!document.querySelector("[data-page=detail]").hidden'),"selected book handoff");
  assert.equal(await evaluate('document.querySelector(".task-book h3").textContent'),selected.title);
  assert.ok(await evaluate(`document.querySelector('.task-book p').textContent.includes(${JSON.stringify(selected.author)})`));
  await until(()=>evaluate('document.querySelector(".task-card").dataset.phase==="getting"'),"demo retrieval");await screenshot("任务进行中.png");
  await click("[data-stop]");assert.equal(await evaluate('document.querySelector(".task-card").dataset.phase'),"cancelled");
  await cmd("Page.reload");await until(()=>evaluate('document.querySelector(".task-card")?.dataset.phase==="cancelled"'),"stopped task survives reload");
  assert.equal(await evaluate('document.querySelector(".task-book h3").textContent'),selected.title);await click("[data-resume]");
  await nav("radar");await nav("bags");assert.ok(await evaluate('!!document.querySelector(".bag-card")'));await click(".bag-card");
  await until(()=>evaluate('document.querySelector(".task-card")?.dataset.phase==="complete"'),"demo complete");await until(()=>evaluate('document.getElementById("toast").hidden'),"completion toast settled");await screenshot("保存书包.png");
  await click("[data-export]");const zip=await until(()=>fs.readdirSync(downloads).find(name=>name.endsWith(".zip")),"actual demonstration ZIP");
  assert.equal(fs.readFileSync(path.join(downloads,zip)).readUInt32LE(0),0x04034b50);
  if(!await evaluate('!!navigator.canShare')){await click("[data-share]");assert.ok(await evaluate('document.getElementById("toast").textContent.includes("不支持")'));}
  await nav("settings");await click('[data-preset="sunset"]');await evaluate('document.getElementById("film").value="36";document.getElementById("film").dispatchEvent(new Event("input",{bubbles:true}))');await until(()=>evaluate('document.getElementById("toast").hidden'),"export toast settled");await screenshot("外观设置.png");
  await cmd("Page.reload");await until(()=>evaluate('document.getElementById("film")?.value==="36"'),"appearance survives reload");
  assert.equal(await evaluate('document.getElementById("mobile-app").dataset.preset'),"sunset");
  await click("#connection-settings");await click("#demo-disconnect");await closed();await nav("search");
  await evaluate('document.getElementById("book-query").value="神通者";document.getElementById("book-query").dispatchEvent(new Event("input",{bubbles:true}))');
  await until(()=>evaluate('!!document.querySelector("[data-restore-connection]")'),"inline error retains title");assert.equal(await evaluate('document.getElementById("book-query").value'),"神通者");await screenshot("连接失败.png");await click("[data-restore-connection]");
  assert.ok(await evaluate('document.querySelectorAll("#search-results .book-row").length>0'));
  await click("#header-action");
  const csv=path.join(run,"booklist.csv");fs.writeFileSync(csv,'书名,作者\r\n"神通者","天蚕土豆"\r\n',"utf8");
  const dom=await cmd("DOM.getDocument"),input=await cmd("DOM.querySelector",{nodeId:dom.root.nodeId,selector:"#booklist-file"});await cmd("DOM.setFileInputFiles",{nodeId:input.nodeId,files:[csv]});
  await until(()=>evaluate('document.getElementById("import-text").value==="神通者 | 天蚕土豆"'),"quoted CSV booklist file");
  await evaluate(`document.getElementById("import-text").value=${JSON.stringify("神通者 | 天蚕土豆\n快照中没有的测试书")}`);await click("#preview-import");
  assert.equal(await evaluate('document.querySelectorAll("#import-results .book-row").length'),1);assert.ok(await evaluate('document.getElementById("import-results").textContent.includes("未找到")'));await click("#close-sheet");await closed();
  await nav("settings");await click("#reset-appearance");await nav("radar");
  const sizes=[[320,720],[430,932],[844,390],[390,530]];
  for(const [width,height] of sizes){await viewport(width,height);await delay(150);await layout(`${width}x${height}`);if(width===844){await screenshot("横屏雷达.png");assert.ok(await evaluate('document.querySelector(".radar-book").getBoundingClientRect().bottom<=document.querySelector(".bottom-nav").getBoundingClientRect().top'),"landscape first book fully visible");}await click("#open-filters");await evaluate('Promise.allSettled(document.getElementById("bottom-sheet").getAnimations().filter(a=>a.animationName==="sheet-enter").map(a=>a.finished))');const bounds=await evaluate('(()=>{const r=document.getElementById("bottom-sheet").getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight}})()');assert.ok(bounds.left>=-1&&bounds.right<=bounds.width+1&&bounds.bottom<=bounds.height+1&&bounds.top>=0,"sheet fits "+width+"x"+height+" "+JSON.stringify(bounds));await click("#close-sheet");await closed();}
  await nav("search");await evaluate('document.getElementById("book-query").value="神通者";document.getElementById("book-query").dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("book-query").focus()');await delay(300);assert.ok(await evaluate('document.getElementById("search-form").getBoundingClientRect().bottom<document.querySelector(".bottom-nav").getBoundingClientRect().top'));assert.equal(await evaluate('getComputedStyle(document.getElementById("book-query")).fontSize'),"16px");
  await viewport(1440,1000);await nav("search");await evaluate('document.getElementById("book-query").value="";document.getElementById("book-query").dispatchEvent(new Event("input",{bubbles:true}))');await delay(300);await screenshot("桌面预览.png");
  await viewport(390,844);await cmd("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});await nav("radar");
  assert.equal(await evaluate('document.getElementById("app-scene").getAnimations().length'),0);assert.equal(await evaluate('document.getElementById("radar-list").getAnimations().length'),0);
  await click("#open-filters");assert.equal(await evaluate('getComputedStyle(document.getElementById("bottom-sheet")).animationName'),"none");await click("#close-sheet");assert.equal(await evaluate('document.getElementById("bottom-sheet").open'),false);
  await cmd("Page.navigate",{url:pathToFileURL(path.join(plan,"prototype/index.html")).href});await until(()=>evaluate('document.readyState==="complete"&&document.querySelectorAll("#inspiration-list .book-row").length===2'),"offline file opens without a server");await nav("radar");assert.equal(await evaluate('document.querySelectorAll(".radar-book").length'),20);await screenshot("本地直接打开.png");
  assert.deepEqual(errors,[]);
  const report={origin,run,zip:path.join(downloads,zip),selected,viewports:[[390,844],...sizes,[1440,1000]],checks:["four-entry navigation","all four dated boards","all filter fields","retained pagination cards","empty result and reset","500ms sheet transition","accurate title and author handoff","stopped demo resumes after reload","switch pages without cancelling task","actual demo ZIP download","appearance persistence","inline connection retry","booklist and quoted CSV import preview","touch target sizes","short and landscape viewports","reduced motion","offline file opens without a server"],errors,scope:"Desktop Chrome emulation and local demonstration only; no Android/iOS device or real novel download."};
  fs.writeFileSync(path.join(run,"result.json"),JSON.stringify(report,null,2));fs.copyFileSync(path.join(run,"result.json"),path.join(plan,"validation.json"));console.log(JSON.stringify(report));
}finally{socket?.close();chrome?.kill();service?.kill();}
