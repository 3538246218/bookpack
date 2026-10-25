// Diagnose only the shipped sea animation. No production sources or task data are changed.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';

const exec = promisify(execFile), project = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out = path.join(project, 'outputs/motion-investigation');
const mode = process.argv[2] || 'web', serial = 'emulator-5554';
const conditionsOnly=process.argv.includes('--conditions-only');
const baselineOnly=process.argv.includes('--baseline-only');
const reportSuffix=conditionsOnly?'-conditions':baselineOnly?'-baseline':'-motion';
const adbExe = 'E:\\dev-tools\\android-sdk\\platform-tools\\adb.exe';
fs.mkdirSync(out, {recursive:true});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const adb = async (...args) => (await exec(adbExe, ['-s', serial, ...args], {windowsHide:true, encoding:'utf8', timeout:25000, maxBuffer:2**22})).stdout.trim();
async function until(fn, label, timeout=30000) {
  const deadline=Date.now()+timeout; let last;
  while(Date.now()<deadline) {try {const value=await fn();if(value)return value;} catch(error){last=error;} await delay(400);}
  throw Error('Timed out: '+label+(last?' '+last.message:''));
}
let server, chrome, socket, cmd, evaluate, originalSystemScale, systemScaleChanged=false; const forwards=[], errors=[], report={mode, observedAt:new Date().toISOString()};
async function attach(target, port) {
  const url=new URL(target.webSocketDebuggerUrl); if(port)url.host='127.0.0.1:'+port;
  socket=new WebSocket(url); await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();
  socket.addEventListener('message',event=>{const row=JSON.parse(event.data);if(pending.has(row.id)){const task=pending.get(row.id);pending.delete(row.id);clearTimeout(task.timer);row.error?task.reject(Error(row.error.message)):task.resolve(row.result);}if(row.method==='Runtime.exceptionThrown')errors.push(row.params.exceptionDetails.exception?.description||row.params.exceptionDetails.text);});
  cmd=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout: '+method));},20000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});
  evaluate=async expression=>{const row=await cmd('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(row.exceptionDetails)throw Error(row.exceptionDetails.exception?.description||row.exceptionDetails.text);return row.result.value;};
  await cmd('Runtime.enable'); await cmd('Page.enable');
}
async function connectNative() {
  await adb('shell','am','start','-W','-n','com.bookpack.mobile/.MainActivity');
  const pid=await until(()=>adb('shell','pidof','com.bookpack.mobile'),'app process');
  const port=await adb('forward','tcp:0','localabstract:webview_devtools_remote_'+pid);forwards.push(port);
  const target=await until(async()=>{const rows=await(await fetch('http://127.0.0.1:'+port+'/json')).json();return rows.find(row=>row.type==='page'&&row.url.startsWith('http://127.0.0.1:'));},'native WebView',60000);
  await attach(target,port);await until(()=>evaluate('!!window.bookpackBack'),'native app scripts');
}
const snapshot=()=>evaluate(`(()=>{const e=document.getElementById('app-scene'),s=getComputedStyle(e),a=e.getAnimations()[0];return {visibility:document.visibilityState,hidden:document.hidden,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,requestedMotion:document.getElementById('toggle-motion').getAttribute('aria-pressed'),still:document.getElementById('mobile-app').dataset.still,waves:document.getElementById('waves').value,animationName:s.animationName,duration:s.animationDuration,playState:s.animationPlayState,transform:s.transform,travel:s.getPropertyValue('--wave-travel'),scale:s.getPropertyValue('--wave-scale'),background:s.backgroundImage,filter:s.filter,animation:a?{playState:a.playState,currentTime:a.currentTime,keyframes:a.effect.getKeyframes()}:null,userAgent:navigator.userAgent,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio}}})()`);
async function sample(label, ms=1800) {
  const before=await snapshot();await delay(ms);const after=await snapshot();
  const result={before,after,transformChanged:before.transform!==after.transform,elapsedAnimationMs:after.animation&&before.animation?after.animation.currentTime-before.animation.currentTime:null};
  report[label]=result;console.log(JSON.stringify({label,requested:after.requestedMotion,reduced:after.reduced,still:after.still,animation:after.animationName,changed:result.transformChanged,elapsedAnimationMs:result.elapsedAnimationMs}));return result;
}
async function configure({waves=56,motion=true}={}) {
  await evaluate(`(()=>{const w=document.getElementById('waves');w.value=${waves};w.dispatchEvent(new Event('input',{bubbles:true}));const m=document.getElementById('toggle-motion');if((m.getAttribute('aria-pressed')==='true')!==${motion})m.click();})()`);
}
async function screenshot(label) {
  if(mode==='native') {
    const row=await exec(adbExe,['-s',serial,'exec-out','screencap','-p'],{windowsHide:true,encoding:'buffer',timeout:20000,maxBuffer:2**24});fs.writeFileSync(path.join(out,mode+'-'+label+'.png'),row.stdout);
  } else {
    const row=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(out,mode+'-'+label+'.png'),Buffer.from(row.data,'base64'));
  }
}
try {
  if(mode==='web') {
    const assets=path.join(out,'shipped-assets');
    server=http.createServer((request,response)=>{
      const url=new URL(request.url,'http://localhost');
      if(url.pathname==='/api/state'){response.writeHead(200,{'Content-Type':'application/json'});response.end('{"batches":[]}');return;}
      const relative=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));const file=path.resolve(assets,relative);
      if(!file.startsWith(assets+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){response.writeHead(404);response.end();return;}
      const mime={'.html':'text/html; charset=utf-8','.js':'application/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png'}[path.extname(file)]||'application/octet-stream';
      response.writeHead(200,{'Content-Type':mime});fs.createReadStream(file).pipe(response);
    });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const profile=path.join(out,'chrome-'+Date.now());fs.mkdirSync(profile,{recursive:true});
    chrome=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',['--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
    const port=await until(()=>fs.existsSync(path.join(profile,'DevToolsActivePort'))&&fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0],'headless Chrome');
    const targets=await(await fetch('http://127.0.0.1:'+port+'/json')).json();await attach(targets.find(row=>row.type==='page'));
    await cmd('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await cmd('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
    await cmd('Page.navigate',{url:'http://127.0.0.1:'+server.address().port+'/'});
    await until(()=>evaluate('document.querySelectorAll("#inspiration-list .book-row").length===2'),'shipped mobile scripts');
  } else {
    console.log('Waiting for task-owned Android emulator.');
    await until(async()=>await adb('shell','getprop','sys.boot_completed')==='1','Android boot',240000);
    report.androidVersion=await adb('shell','getprop','ro.build.version.release');
    report.systemAnimationScale=await adb('shell','settings','get','global','animator_duration_scale');
    await connectNative();
  }
  report.originalState=await snapshot();
  report.originalPreferences=await evaluate('window.AndroidHost?window.AndroidHost.getPreferences():null');
  if(baselineOnly) {
    await sample('baseline',1200);
  } else {
  await configure();await evaluate('location.hash="settings"');
  await until(()=>evaluate('document.getElementById("page-title").textContent==="设置"'),'settings view');
  if(conditionsOnly) {
    if(mode!=='native')throw Error('The system-setting check requires the task-owned emulator.');
    await cmd('Emulation.setEmulatedMedia',{features:[]});
    originalSystemScale=await adb('shell','settings','get','global','animator_duration_scale');report.originalSystemScale=originalSystemScale;
    await sample('originalSystemSetting',1000);
    await adb('shell','settings','put','global','animator_duration_scale','0');systemScaleChanged=true;
    await delay(1000);report.androidScaleWhileOff=await adb('shell','settings','get','global','animator_duration_scale');
    await sample('actualAndroidAnimationsOff',1000);await screenshot('android-animations-off');
    socket.close();await adb('shell','am','force-stop','com.bookpack.mobile');await connectNative();await delay(1000);await sample('actualAndroidAnimationsOffAfterProcessRestart',1000);
    if(originalSystemScale==='null')await adb('shell','settings','delete','global','animator_duration_scale');else await adb('shell','settings','put','global','animator_duration_scale',originalSystemScale);
    systemScaleChanged=false;await delay(1000);await sample('actualAndroidSettingRestored',1000);
    await cmd('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});await configure();await evaluate('location.hash="settings"');await delay(1000);
    await evaluate("(()=>{const a=document.getElementById('app-scene').getAnimations()[0];a.pause();a.currentTime=0;getComputedStyle(document.getElementById('app-scene')).transform;})()");await delay(1200);await screenshot('render-phase-zero');
    await evaluate("(()=>{const a=document.getElementById('app-scene').getAnimations()[0];a.currentTime=a.effect.getTiming().duration/2;getComputedStyle(document.getElementById('app-scene')).transform;})()");await delay(1200);await screenshot('render-phase-half');
    report.renderCheck={frames:['native-render-phase-zero.png','native-render-phase-half.png'],phaseSeparationSeconds:7.8};
  } else {
  await delay(700);await sample('defaultRunning',2200);await screenshot('default');
  await configure({waves:0});await sample('zeroWaves',1800);
  await configure({waves:100});await sample('maximumWaves',1800);
  await configure({motion:false});await sample('explicitlyOff',1200);
  await configure();await cmd('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await delay(500);await sample('reducedMotion',1200);await screenshot('reduced-motion');
  await evaluate('document.getElementById("toggle-motion").click();document.getElementById("toggle-motion").click()');await sample('reducedMotionAfterOffOn',1000);
  await cmd('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});await delay(500);await sample('restoredMotion',1800);
  report.deterministicFrames=await evaluate(`(()=>{const e=document.getElementById('app-scene'),a=e.getAnimations()[0],duration=a.effect.getTiming().duration;if(!a)return [];a.pause();return [0,.1,.5,1].map(fraction=>{a.currentTime=duration*fraction;return {fraction,transform:getComputedStyle(e).transform,progress:a.effect.getComputedTiming().progress}})})()`);
  await evaluate('document.getElementById("app-scene").getAnimations()[0]?.play()');
  if(mode==='native') {
    await adb('shell','input','keyevent','3');await delay(700);report.background=await snapshot();
    await adb('shell','am','start','-W','-n','com.bookpack.mobile/.MainActivity');await delay(800);await sample('afterBackgroundReturn',1800);
  }
  }
  }
  report.errors=errors;
  fs.writeFileSync(path.join(out,mode+reportSuffix+'.json'),JSON.stringify(report,null,2));console.log('Motion report written: '+mode);
} catch(error) {
  report.error=error.stack;fs.writeFileSync(path.join(out,mode+reportSuffix+'.json'),JSON.stringify(report,null,2));throw error;
} finally {
  if(systemScaleChanged)try{if(originalSystemScale==='null')await adb('shell','settings','delete','global','animator_duration_scale');else await adb('shell','settings','put','global','animator_duration_scale',originalSystemScale);}catch{}
  if(evaluate&&mode==='native'&&report.originalPreferences)try{await cmd('Emulation.setEmulatedMedia',{features:[]});await evaluate(`window.AndroidHost.savePreferences(${JSON.stringify(report.originalPreferences)})`);await cmd('Page.reload');}catch{}
  socket?.close();chrome?.kill();server?.close();
  for(const port of forwards)try{await adb('forward','--remove','tcp:'+port);}catch{}
}
