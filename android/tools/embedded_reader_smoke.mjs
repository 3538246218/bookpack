import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import http from 'node:http';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {adb,until,connect,api,screenshot,disconnect} from './legado_native_smoke.mjs';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),out=path.join(root,'outputs');
let samples,fixture;
const report={version:'0.4.0',reader:'embedded Legado 3.26101819',checks:[]};
const rows=xml=>[...xml.matchAll(/<node\b(?:[^>"']|"[^"]*"|'[^']*')*>/g)].map(row=>Object.fromEntries([...row[0].matchAll(/([\w-]+)=(["'])([\s\S]*?)\2/g)].map(item=>[item[1],item[3]])));
async function ui(){await adb('shell','uiautomator','dump','--compressed','/sdcard/bookpack-embedded.xml');return rows(await adb('shell','cat','/sdcard/bookpack-embedded.xml'));}
async function tap(node){const b=[...node.bounds.matchAll(/\d+/g)].map(n=>+n[0]);await adb('shell','input','tap',String((b[0]+b[2])>>1),String((b[1]+b[3])>>1));}
async function foreground(fragment){return (await adb('shell','dumpsys','activity','activities')).split('\n').some(row=>/topResumedActivity|Resumed:/.test(row)&&row.includes('com.bookpack.mobile/'+fragment));}
async function shelf(){const text=await adb('shell','content','query','--uri','content://com.bookpack.mobile.readerProvider/books/query');const result=JSON.parse(text.slice(text.indexOf('result=')+7));assert.equal(result.isSuccess,true);return result.data;}
async function book(kind){return (await shelf()).find(row=>row.name.includes(samples[kind].title));}
const position=record=>[record.durChapterIndex||0,record.durChapterPos||0];
async function page(){const all=await ui();return {footer:all.find(row=>row['resource-id']?.endsWith('/tv_footer_right'))?.text||'',body:all.find(row=>row['resource-id']?.endsWith('/content_text_view'))?.['content-desc']||''};}
async function home(){await adb('shell','am','start','-W','-f','0x20020000','-n','com.bookpack.mobile/.MainActivity');const evaluate=await connect();await until(()=>evaluate('!!window.BookpackLegado&&!!window.BookpackReader'),'Bookpack home');return evaluate;}
async function open(kind){const evaluate=await home();await evaluate(`BookpackLegado.choose('legado');window.__embeddedResult=null;window.__originalReaderResult=window.onLegadoResult;window.onLegadoResult=result=>{window.__embeddedResult=result;window.__originalReaderResult(result)};BookpackReader.open(${JSON.stringify(samples[kind].id)});true`);const result=await until(()=>evaluate('window.__embeddedResult'),'embedded import',90000);assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.embedded,true);await evaluate('window.onLegadoResult=window.__originalReaderResult');await until(()=>foreground('io.legado.app.ui.book.read.ReadBookActivity'),'reader is inside Bookpack');let all=await ui();if(all.some(row=>row.text==='Screen touch control'))await adb('shell','input','tap','990','126');await until(async()=>(await book(kind))?.totalChapterNum>0,'embedded parsing');return result;}
async function acquireSamples(){
 const suffix=String(Date.now()),directory=path.join(out,'embedded-fixtures-'+suffix),exec=promisify(execFile);
 const names=JSON.parse((await exec('C:/Users/PC/AppData/Local/Programs/Python/Python312/python.exe',['-X','utf8',path.join(root,'tools/legado_fixtures.py'),directory,suffix],{windowsHide:true,encoding:'utf8'})).stdout);
 fixture=http.createServer((request,response)=>{const file=request.url==='/letter.txt'?'letter.txt':'illustrated.epub',bytes=fs.readFileSync(path.join(directory,file));response.writeHead(200,{'Content-Type':file.endsWith('.txt')?'text/plain; charset=gb18030':'application/epub+zip','Content-Length':bytes.length});response.end(bytes);});await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
 const books={};
 for(const [kind,file] of [['txt','letter.txt'],['epub','illustrated.epub']]){
   const batch=await api('api/start',{text:names[kind]+' | http://10.0.2.2:'+fixture.address().port+'/'+file,mode:'links',name:names[kind],concurrency:1});
   const done=await until(async()=>{const row=(await api('api/state')).batches.find(row=>row.id===batch.id);return row?.status==='complete'&&row;},'acquire original '+kind);
   assert.equal(done.tasks[0].source_sha256,crypto.createHash('sha256').update(fs.readFileSync(path.join(directory,file))).digest('hex'));
   books[kind]=(await api('api/reader/library')).books.find(row=>row.batch_id===batch.id);assert.ok(books[kind]);
 }
 fixture.close();fixture=null;return books;
}
let externalDisabled=false;
try{
 await until(async()=>await adb('shell','getprop','sys.boot_completed')==='1','Android boot',240000);
 const old=JSON.parse(await adb('shell','run-as','com.bookpack.mobile','cat','files/bookpack/任务记录.json')).batches.map(row=>row.id);
 console.log(await adb('install','-r','E:/dev-tools/builds/bookpack-android/app/build/outputs/apk/debug/app-debug.apk'));
 let evaluate=await home();assert.equal((await api('api/mobile/info')).version,'0.4.0');const current=new Set((await api('api/state')).batches.map(row=>row.id));assert.ok(old.every(id=>current.has(id)));report.previousTasksPreserved=old.length;report.checks.push('upgrade preserves existing tasks and books');
 if((await adb('shell','pm','list','packages','com.legado.app.release')).includes('com.legado.app.release')){await adb('shell','pm','disable-user','--user','0','com.legado.app.release');externalDisabled=true;}
 const status=await evaluate('BookpackLegado.refresh()');assert.equal(status.available,true);assert.equal(status.embedded,true);assert.equal(status.packageName,'com.bookpack.mobile');report.checks.push('embedded reader available with standalone Legado disabled');
 const launchers=(await adb('shell','cmd','package','query-activities','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER','-p','com.bookpack.mobile')).split('\n').filter(row=>row.trim().startsWith('com.bookpack.mobile/'));assert.equal(launchers.length,1);report.checks.push('one package has one launcher entry');
 await evaluate('AndroidHost.showReaderLicense()');let all=await ui();assert.ok(all.some(row=>row.text?.includes('GNU GENERAL PUBLIC LICENSE')));await screenshot('安卓0.4-开源许可.png');await tap(all.find(row=>row['resource-id']==='android:id/button1'));report.checks.push('GPL license can be viewed offline');
 samples=await acquireSamples();report.checks.push('original TXT and illustrated EPUB acquired on Android');
 assert.equal((await open('txt')).resumed,false);const first=await page();assert.ok(first.body.includes('邮差沿着山路走来'));await screenshot('安卓0.4-内置Legado-TXT.png');report.checks.push('TXT rendered by native reader inside Bookpack');
 await adb('shell','input','keyevent','25');const next=await until(async()=>{const row=await book('txt');return row.durChapterPos>0&&row;},'volume next page');const second=await page();assert.notEqual(second.footer,first.footer);
 await adb('shell','input','keyevent','24');await until(async()=>JSON.stringify(position(await book('txt')))==='[0,0]','volume previous page');assert.equal((await page()).footer,first.footer);report.volume={first:first.footer,next:second.footer,position:position(next)};report.checks.push('volume down next page','volume up previous page');
 await adb('shell','input','keyevent','25');await until(async()=>(await book('txt')).durChapterPos>0,'saved TXT page');const txt=await book('txt');await adb('shell','input','keyevent','4');await until(()=>foreground('.MainActivity'),'back returns to Bookpack');report.checks.push('native Back returns to Bookpack');
 await adb('shell','am','force-stop','com.bookpack.mobile');assert.equal((await open('txt')).resumed,true);assert.deepEqual(position(await book('txt')),position(txt));assert.equal((await shelf()).filter(row=>row.name.includes(samples.txt.title)).length,1);report.txtResume=position(txt);report.checks.push('TXT progress survives restart without duplicates');
 assert.equal((await open('epub')).resumed,false);const epubFirst=await page();assert.ok(epubFirst.body.includes('原创 EPUB 联调样文'));await screenshot('安卓0.4-内置Legado-EPUB插图.png');let epub=await book('epub');for(let n=0;n<8&&epub.durChapterIndex<1;n++){const before=position(epub);await adb('shell','input','keyevent','25');await until(async()=>JSON.stringify(position(await book('epub')))!==JSON.stringify(before),'EPUB next page');epub=await book('epub');}assert.equal(epub.durChapterIndex,1);assert.ok((await page()).body.includes('潮声停下后'));await screenshot('安卓0.4-内置Legado-EPUB第二章.png');report.checks.push('illustrated EPUB and chapter navigation inside Bookpack');
 await adb('shell','am','force-stop','com.bookpack.mobile');assert.equal((await open('epub')).resumed,true);assert.deepEqual(position(await book('epub')),position(epub));assert.equal((await shelf()).filter(row=>row.name.includes(samples.epub.title)).length,1);report.epubResume=position(epub);report.checks.push('EPUB progress survives restart without duplicates');
 report.scope='Android 15 API 35 x86_64 emulator; standalone reader disabled throughout the reading checks; no physical ARM phone.';report.passed=true;fs.writeFileSync(path.join(out,'embedded-reader-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){try{fs.writeFileSync(path.join(out,'embedded-reader-failure.log'),await adb('logcat','-d','-s','AndroidRuntime','Bookpack','python.stderr'));await screenshot('安卓0.4-检查现场.png');}catch{}throw error;}
finally{fixture?.close();if(externalDisabled)await adb('shell','pm','enable','com.legado.app.release');disconnect();}
