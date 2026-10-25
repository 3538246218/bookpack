import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {fileURLToPath} from "node:url";
import assert from "node:assert/strict";
const exec=promisify(execFile),project=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out=path.join(project,"outputs"),previews=path.join(out,"previews"),adbExe="E:\\dev-tools\\android-sdk\\platform-tools\\adb.exe",serial="emulator-5554";
for(const directory of[out,previews])fs.mkdirSync(directory,{recursive:true});
const adb=async(...args)=>(await exec(adbExe,["-s",serial,...args],{windowsHide:true,encoding:"utf8",maxBuffer:16*1024*1024,timeout:30000})).stdout.trim();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label,timeout=30000){const deadline=Date.now()+timeout;let last;while(Date.now()<deadline){try{const value=await fn();if(value)return value;}catch(error){last=error;}await delay(250);}throw Error("Timed out: "+label+(last?" "+last.message:""));}
let socket,apiOrigin,fixture;const errors=[];
async function connect(){
 const pid=await adb("shell","pidof","com.bookpack.mobile");assert.ok(pid);
 const port=await adb("forward","tcp:0","localabstract:webview_devtools_remote_"+pid);
 const target=await until(async()=>{const rows=await(await fetch("http://127.0.0.1:"+port+"/json")).json();return rows.find(row=>row.type==="page"&&row.url.startsWith("http://127.0.0.1:"));},"Python and WebView startup",60000);
 const ws=new URL(target.webSocketDebuggerUrl);ws.host="127.0.0.1:"+port;socket=new WebSocket(ws);await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
 let counter=0;const pending=new Map();socket.addEventListener("message",event=>{const msg=JSON.parse(event.data);if(pending.has(msg.id)){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(Error(msg.error.message)):p.resolve(msg.result);}if(msg.method==="Runtime.exceptionThrown")errors.push(msg.params.exceptionDetails.exception?.description||msg.params.exceptionDetails.text);});
 const cmd=(method,params={})=>new Promise((resolve,reject)=>{const id=++counter;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 async function evaluate(expression){const r=await cmd("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
 await cmd("Runtime.enable");
 const nativePort=new URL(target.url).port,apiPort=await adb("forward","tcp:0","tcp:"+nativePort);apiOrigin="http://127.0.0.1:"+apiPort+"/";
 return {evaluate,cmd,pid};
}
async function api(route,data){const r=await fetch(apiOrigin+route,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});const payload=await r.json();assert.ok(r.ok,JSON.stringify(payload));return payload;}
async function screenshot(name){const r=await exec(adbExe,["-s",serial,"exec-out","screencap","-p"],{windowsHide:true,encoding:"buffer",maxBuffer:16*1024*1024,timeout:20000});fs.writeFileSync(path.join(previews,name),r.stdout);}
async function uiXml(){await adb("shell","uiautomator","dump","--compressed","/sdcard/bookpack-ui.xml");return adb("shell","cat","/sdcard/bookpack-ui.xml");}
function nodes(xml){return [...xml.matchAll(/<node\b[^>]*>/g)].map(row=>Object.fromEntries([...row[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(item=>[item[1],item[2]])));}
async function tapNode(node){const b=[...node.bounds.matchAll(/\d+/g)].map(m=>Number(m[0]));await adb("shell","input","tap",String(Math.round((b[0]+b[2])/2)),String(Math.round((b[1]+b[3])/2)));}
try{
 console.log("Waiting for Android 35 to boot…");await until(async()=>await adb("shell","getprop","sys.boot_completed")==="1","Android boot",240000);
 const apk="E:\\dev-tools\\builds\\bookpack-android\\app\\build\\outputs\\apk\\debug\\app-debug.apk";
 console.log(await adb("install","-r",apk));await adb("shell","am","start","-W","-n","com.bookpack.mobile/.MainActivity");
 let {evaluate}=await connect();await until(()=>evaluate('document.querySelectorAll("#inspiration-list .book-row").length===2'),"native app home");
 const ping=await api("api/ping");assert.ok(ping.home.startsWith("/data/"),ping.home);await screenshot("安卓实机环境-首页.png");
 const query=await api("api/suggest",{query:"無職",online:false,client_id:"native-check",generation:1});assert.ok(query.suggestions.some(book=>book.title.includes("无职")),"traditional Chinese normalization on Android");
 await api("api/sources",{local_dirs:[],wikisource:false,web_sources:[],books:[{title:"安卓样文",author:"书包",source:"内置原创演示",url:new URL("demo/original.txt",await evaluate("location.href")).href}]});
 await evaluate('document.getElementById("book-query").value="安卓样文";document.getElementById("book-query").dispatchEvent(new Event("input",{bubbles:true}))');await until(()=>evaluate('!!document.querySelector("#search-results .book-row")'),"native suggestions");await evaluate('document.querySelector("#search-results .book-row").click()');
 const first=await until(async()=>{const s=await api("api/state");return s.batches.find(batch=>batch.status==="complete");},"native Python retrieval and archive");await until(()=>evaluate('document.querySelector(".task-card")?.dataset.phase==="complete"'),"native completion UI");assert.equal(first.tasks[0].title,"安卓样文");assert.equal(first.tasks[0].author,"书包");await screenshot("安卓实机环境-真实任务.png");
 const exportName="bookpack-native-"+Date.now()+".zip";
 await evaluate(`window.AndroidHost.save(${JSON.stringify("/api/archive/"+first.id)},${JSON.stringify(exportName)})`);await delay(900);
 const xml=await uiXml();fs.writeFileSync(path.join(out,"document-ui.xml"),xml);await screenshot("安卓系统-保存面板.png");
 const save=nodes(xml).find(node=>node.enabled==="true"&&(node["resource-id"]?.endsWith("/button_save")||/^(Save|SAVE|保存)$/.test(node.text)));
 assert.ok(save,"native document picker has a Save action");await tapNode(save);
 await until(async()=>{const files=await adb("shell","ls","/sdcard/Download");return files.includes(exportName);},"file saved to Android Downloads");
 const expected=Buffer.from(await(await fetch(apiOrigin+"api/archive/"+first.id)).arrayBuffer());
 await until(async()=>Number(await adb("shell","stat","-c","%s","/sdcard/Download/"+exportName))===expected.length,"native output stream finishes writing");
 const exported=path.join(out,"native-export.zip");await adb("pull","/sdcard/Download/"+exportName,exported);
 assert.ok(fs.readFileSync(exported).equals(expected),"saved ZIP matches actual native archive byte for byte");
 console.log("Native retrieval, system file picker and saved ZIP verified.");
 const body=Buffer.from("书包安卓后台获取验证\n\n"+Array.from({length:1400},(_,i)=>`第 ${i+1} 段：这是专门创作的功能验证样文，用于检查手机退到后台后是否继续传输。\n`).join("")+"\n原创功能样文结束。\n","utf8");
 fixture=http.createServer((req,res)=>{res.writeHead(200,{"Content-Type":"text/plain; charset=utf-8","Content-Length":body.length});let offset=0;const timer=setInterval(()=>{if(offset>=body.length){clearInterval(timer);res.end();return;}res.write(body.subarray(offset,offset+4096));offset+=4096;},150);res.on("close",()=>clearInterval(timer));});await new Promise(resolve=>fixture.listen(0,"127.0.0.1",resolve));const fixturePort=fixture.address().port;
 await evaluate('document.querySelector(".bottom-nav [data-nav=search]").click();document.getElementById("header-action").click()');await until(()=>evaluate('!!document.getElementById("import-mode")'),"native import sheet");
 await evaluate(`document.getElementById('import-mode').value='links';document.getElementById('import-text').value=${JSON.stringify("后台样文 | http://10.0.2.2:"+fixturePort+"/fixture.txt")};document.getElementById('preview-import').click()`);await until(()=>evaluate('!!document.getElementById("start-import")'),"native link preview");await evaluate('document.getElementById("start-import").click()');
 const background=await until(async()=>{const s=await api("api/state");return s.batches.find(batch=>batch.tasks[0].title==="后台样文"&&batch.tasks[0].bytes>0);},"background sample started");await adb("shell","input","keyevent","3");const services=await adb("shell","dumpsys","activity","services","com.bookpack.mobile");assert.ok(services.includes("TaskService"));
 const backgroundDone=await until(async()=>{const s=await api("api/state");return s.batches.find(batch=>batch.id===background.id&&batch.status==="complete");},"download continues while app is backgrounded",45000);assert.equal(backgroundDone.tasks[0].source_sha256,crypto.createHash("sha256").update(body).digest("hex"));
 await adb("shell","am","start","-W","-n","com.bookpack.mobile/.MainActivity");await delay(600);socket.close();({evaluate}=await connect());
 await evaluate('document.querySelector(".bottom-nav [data-nav=settings]").click();document.querySelector("[data-preset=sunset]").click();document.getElementById("film").value="36";document.getElementById("film").dispatchEvent(new Event("input",{bubbles:true}))');await delay(300);socket.close();await adb("shell","am","force-stop","com.bookpack.mobile");await adb("shell","am","start","-W","-n","com.bookpack.mobile/.MainActivity");({evaluate}=await connect());
 await until(()=>evaluate('document.getElementById("film")?.value==="36"&&document.getElementById("mobile-app").dataset.preset==="sunset"'),"native preferences survive process restart");assert.equal((await api("api/state")).batches.length,2);
 await evaluate('document.querySelector(".bottom-nav [data-nav=radar]").click()');await until(()=>evaluate('location.hash==="#radar"&&document.getElementById("page-title").textContent==="番茄雷达"&&document.querySelectorAll(".radar-book").length===20&&!document.getElementById("radar-list").inert'),"native radar");await delay(1800);await screenshot("安卓实机环境-雷达.png");
 assert.deepEqual(errors,[]);const report={serial,android:await adb("shell","getprop","ro.build.version.release"),apiLevel:await adb("shell","getprop","ro.build.version.sdk"),home:ping.home,firstBatch:first.id,backgroundBatch:background.id,exported,exportSha256:crypto.createHash("sha256").update(expected).digest("hex"),checks:["APK installs and launches","Python executes on Android","traditional Chinese matching","real original TXT retrieval and ZIP","native system Save picker","exported ZIP byte equality","foreground service keeps background download","background source hash equality","preferences and tasks survive process restart","native radar UI"],errors,scope:"Android 35 x86_64 emulator; no physical ARM phone used."};fs.writeFileSync(path.join(out,"native-validation.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){try{fs.writeFileSync(path.join(out,"native-failure.log"),await adb("logcat","-d","-t","500"));await screenshot("安卓环境-检查现场.png");}catch{}throw error;}
finally{socket?.close();fixture?.close();}
