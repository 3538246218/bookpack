import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';

const exec=promisify(execFile),project=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out=path.join(project,'outputs'),previews=path.join(out,'previews');
const adbExe='E:/dev-tools/android-sdk/platform-tools/adb.exe',serial='emulator-5554';
const python='C:/Users/PC/AppData/Local/Programs/Python/Python312/python.exe';
fs.mkdirSync(previews,{recursive:true});
const adb=async(...args)=>(await exec(adbExe,['-s',serial,...args],{windowsHide:true,encoding:'utf8',maxBuffer:12*1024*1024,timeout:60000})).stdout.trim();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label,timeout=45000){const end=Date.now()+timeout;let last;while(Date.now()<end){try{const result=await fn();if(result)return result;}catch(error){last=error;}await delay(350);}throw Error('Timed out: '+label+(last?' '+last.message:''));}
let socket,fixture,origin;
async function connect(){
 socket?.close();
 const pid=await adb('shell','pidof','com.bookpack.mobile');assert.ok(pid);
 const port=await adb('forward','tcp:0','localabstract:webview_devtools_remote_'+pid);
 const target=await until(async()=>{const rows=await(await fetch('http://127.0.0.1:'+port+'/json',{signal:AbortSignal.timeout(3000)})).json();return rows.find(row=>row.type==='page'&&row.url.startsWith('http://127.0.0.1:'));},'native WebView startup',90000);
 const endpoint=new URL(target.webSocketDebuggerUrl);endpoint.host='127.0.0.1:'+port;socket=new WebSocket(endpoint);await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 let counter=0;const pending=new Map();socket.addEventListener('message',event=>{const result=JSON.parse(event.data);if(pending.has(result.id)){const row=pending.get(result.id);pending.delete(result.id);clearTimeout(row.timer);result.error?row.reject(Error(result.error.message)):row.resolve(result.result);}});
 async function evaluate(expression){const id=++counter;const response=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(Error('WebView evaluation timed out'));},20000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,returnByValue:true,awaitPromise:true}}));});if(response.exceptionDetails)throw Error(response.exceptionDetails.exception?.description||response.exceptionDetails.text);return response.result.value;}
 const nativePort=new URL(target.url).port,apiPort=await adb('forward','tcp:0','tcp:'+nativePort);origin='http://127.0.0.1:'+apiPort+'/';return evaluate;
}
async function api(route,body){const response=await fetch(origin+route,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(15000)});const result=await response.json();assert.ok(response.ok,JSON.stringify(result));return result;}
async function screenshot(name){const result=await exec(adbExe,['-s',serial,'exec-out','screencap','-p'],{windowsHide:true,encoding:'buffer',maxBuffer:16*1024*1024,timeout:20000});fs.writeFileSync(path.join(previews,name),result.stdout);}

export {adb,until,delay,connect,api,screenshot};
export function disconnect(){socket?.close();}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
try{
 console.log('Waiting for the task-owned Android 35 device…');
 await until(async()=>await adb('shell','getprop','sys.boot_completed')==='1','Android boot',240000);
 let previousIds=[];try{previousIds=JSON.parse(await adb('shell','run-as','com.bookpack.mobile','cat','files/bookpack/任务记录.json')).batches.map(row=>row.id);}catch{}
 const apk='E:/dev-tools/builds/bookpack-android/app/build/outputs/apk/debug/app-debug.apk';
 console.log(await adb('install','-r',apk));await adb('shell','am','start','-W','-n','com.bookpack.mobile/.MainActivity');
 const evaluate=await connect();await until(()=>evaluate('!!window.BookpackLegado&&!!window.BookpackReader'),'reader integration ready');
 assert.equal((await api('api/mobile/info')).version,'0.3.0');const installedIds=new Set((await api('api/state')).batches.map(row=>row.id));assert.ok(previousIds.every(id=>installedIds.has(id)));
 const state={version:'0.3.0',serial,android:await adb('shell','getprop','ro.build.version.release'),previousTasksPreserved:previousIds.length,home:(await api('api/ping')).home,checks:['APK installs and launches','existing task data survives upgrade']};
 const before=await evaluate('BookpackLegado.refresh()');
 if(!before.available){
   const existing=(await api('api/reader/library')).books[0];assert.ok(existing);
   await evaluate(`BookpackLegado.choose('legado');BookpackReader.open(${JSON.stringify(existing.id)});true`);
   await until(()=>evaluate('document.getElementById("legado-dialog").open'),'missing native reader prompt');
   assert.ok(await evaluate('document.getElementById("legado-dialog-message").textContent.includes("还没有安装")'));
   await screenshot('安卓0.3-Legado未安装.png');
   await evaluate('document.getElementById("legado-use-builtin").click()');await until(()=>evaluate('!document.getElementById("reader-view").hidden&&!document.getElementById("reader-bookmark").disabled'),'explicit built-in fallback');
   await evaluate('document.getElementById("reader-back").click()');await until(()=>evaluate('document.getElementById("reader-view").hidden'),'close fallback reader');
   state.checks.push('missing Legado prompt on Android','explicit built-in fallback on Android');
 }
 const suffix=String(Date.now()),directory=path.join(out,'legado-fixtures-'+suffix);
 const names=JSON.parse((await exec(python,['-X','utf8',path.join(project,'tools/legado_fixtures.py'),directory,suffix],{windowsHide:true,encoding:'utf8'})).stdout);
 fixture=http.createServer((request,response)=>{const filename=request.url==='/letter.txt'?'letter.txt':'illustrated.epub',body=fs.readFileSync(path.join(directory,filename));response.writeHead(200,{'Content-Type':filename.endsWith('.txt')?'text/plain; charset=gb18030':'application/epub+zip','Content-Length':body.length});response.end(body);});await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
 state.books={};
 for(const [kind,filename] of [['txt','letter.txt'],['epub','illustrated.epub']]){
   const batch=await api('api/start',{text:names[kind]+' | http://10.0.2.2:'+fixture.address().port+'/'+filename,mode:'links',name:names[kind],concurrency:1});
   const complete=await until(async()=>{const item=(await api('api/state')).batches.find(row=>row.id===batch.id);if(item?.status==='failed')throw Error(JSON.stringify(item));return item?.status==='complete'&&item;},'actual '+kind+' acquisition');
   assert.equal(complete.tasks[0].source_sha256,crypto.createHash('sha256').update(fs.readFileSync(path.join(directory,filename))).digest('hex'));
   const book=(await api('api/reader/library')).books.find(row=>row.batch_id===batch.id);assert.ok(book);state.books[kind]={...book,batch_id:batch.id};
 }
 state.checks.push('original GB18030 TXT and illustrated EPUB acquired on Android');
 const legadoApk='E:/dev-tools/downloads/legado_app_3.26101819_universal_release.apk';assert.equal(crypto.createHash('sha256').update(fs.readFileSync(legadoApk)).digest('hex'),'ccbf4871ceecd7c31023ea7ed7201ca1623509c07fc287ab2f1afc3e7e09eb8e');
 console.log(await adb('install','-r',legadoApk));
 const connected=await evaluate('BookpackLegado.refresh()');assert.equal(connected.available,true);assert.equal(connected.packageName,'com.legado.app.release');state.legado=connected;state.checks.push('real upstream Legado package detected');
 await evaluate('document.querySelector(".bottom-nav [data-nav=settings]").click();document.querySelector("[data-reader-engine=legado]").click();document.querySelector("[data-reader-engine=legado]").scrollIntoView({block:"center"})');await screenshot('安卓0.3-Legado已连接.png');
 await evaluate(`window.__nativeLegadoResult=null;window.__realLegadoResult=window.onLegadoResult;window.onLegadoResult=result=>{window.__nativeLegadoResult=result;window.__realLegadoResult(result)};BookpackReader.open(${JSON.stringify(state.books.txt.id)});true`);
 await until(async()=>(await adb('shell','dumpsys','activity','activities')).includes('com.legado.app.release/io.legado.app.ui.'),'actual Legado activity');
 state.phase='first_handoff_started';state.fixtureDirectory=directory;fs.writeFileSync(path.join(out,'legado-native-session.json'),JSON.stringify(state,null,2));await screenshot('安卓0.3-Legado首次送书.png');console.log(JSON.stringify(state));
}catch(error){try{fs.writeFileSync(path.join(out,'legado-native-failure.log'),await adb('logcat','-d','-s','AndroidRuntime','Bookpack','python.stderr'));await screenshot('安卓0.3-Legado检查现场.png');}catch{}throw error;}
finally{socket?.close();fixture?.close();}
}
