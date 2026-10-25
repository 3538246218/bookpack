// Read-only rendering of a real inspected task; never starts a download.
import fs from "node:fs";
import path from "node:path";
import {spawn} from "node:child_process";
import {fileURLToPath} from "node:url";
import assert from "node:assert/strict";
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const origin=JSON.parse(fs.readFileSync(path.join(root,"运行地址.json"),"utf8")).url;
const profile=path.join(root,".ui-run","quality-"+Date.now());
fs.mkdirSync(profile,{recursive:true});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(callback,label,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){const value=await callback();if(value)return value;await delay(100);}throw new Error(label);}
let chrome,socket;
try{
 chrome=spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",["--headless=new","--enable-unsafe-swiftshader","--no-first-run","--no-default-browser-check","--remote-debugging-port=0","--user-data-dir="+profile,"about:blank"],{windowsHide:true,stdio:"ignore"});
 const port=await until(()=>{try{return fs.readFileSync(path.join(profile,"DevToolsActivePort"),"utf8").split("\n")[0];}catch(error){if(["ENOENT","EBUSY","EPERM"].includes(error.code))return false;throw error;}},"Chrome ready");
 const targets=await(await fetch("http://127.0.0.1:"+port+"/json")).json();
 socket=new WebSocket(targets.find(row=>row.type==="page").webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
 let count=0;const requests=new Map(),errors=[];
 socket.addEventListener("message",event=>{const message=JSON.parse(event.data);if(requests.has(message.id)){const request=requests.get(message.id);requests.delete(message.id);message.error?request.reject(new Error(message.error.message)):request.resolve(message.result);}if(message.method==="Runtime.exceptionThrown")errors.push(message.params.exceptionDetails.text);});
 const command=(method,params={})=>new Promise((resolve,reject)=>{const id=++count;requests.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 async function evaluate(expression){const result=await command("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result?.value;}
 async function shot(name){await delay(250);const value=await command("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});fs.writeFileSync(path.join(root,"预览",name),Buffer.from(value.data,"base64"));}
 await command("Runtime.enable");await command("Page.enable");
 await command("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});
 await command("Emulation.setDeviceMetricsOverride",{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await command("Page.navigate",{url:origin+"?batch=fa677fa4d2b0"});
 await until(()=>evaluate('document.querySelector(".book-name")?.textContent==="牧神记" && !document.getElementById("retry").hidden'),"inspected task visible");
 assert.equal(await evaluate('document.getElementById("retry").textContent'),"修复缺页");
 assert.equal(await evaluate('document.getElementById("result-status").textContent'),"已生成文件，存在缺损待修复");
 assert.ok(await evaluate('document.querySelector(".task-error").textContent.includes("占位")'));
 assert.equal(await evaluate('document.getElementById("download").textContent.trim()'),"下载现有文件");
 await evaluate('document.querySelector("details[data-details-key$=quality] > summary").click()');
 await shot("下载缺损提示.png");
 await command("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
 await shot("下载缺损窄屏.png");
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:"passed",checks:["real task deep link","confirmed missing-page warning","repair action","existing-file download label","390px layout"],previews:["下载缺损提示.png","下载缺损窄屏.png"]}));
}finally{socket?.close();chrome?.kill();}
