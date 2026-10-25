import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {adb,until,connect,screenshot,disconnect,delay} from './legado_native_smoke.mjs';

const project=path.dirname(path.dirname(fileURLToPath(import.meta.url))),out=path.join(project,'outputs');
const state=JSON.parse(fs.readFileSync(path.join(out,'legado-native-session.json'),'utf8'));
const packageName='com.legado.app.release';
function nodes(xml){return [...xml.matchAll(/<node\b[^>]*>/g)].map(row=>Object.fromEntries([...row[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(item=>[item[1],item[2]])));}
async function ui(){await adb('shell','uiautomator','dump','--compressed','/sdcard/legado-ui.xml');return nodes(await adb('shell','cat','/sdcard/legado-ui.xml'));}
async function tap(node){const b=[...node.bounds.matchAll(/\d+/g)].map(row=>Number(row[0]));await adb('shell','input','tap',String((b[0]+b[2])>>1),String((b[1]+b[3])>>1));}
async function books(){const output=await adb('shell','content','query','--uri','content://'+packageName+'.readerProvider/books/query');const result=JSON.parse(output.slice(output.indexOf('result=')+7));return result.isSuccess?result.data:[];}
async function record(kind){return (await books()).find(book=>book.name.includes(state.books[kind].title));}
const position=book=>[book.durChapterIndex||0,book.durChapterPos||0];
async function page(){const rows=await ui();return {footer:rows.find(row=>row['resource-id']?.endsWith('/tv_footer_right'))?.text||'',content:rows.find(row=>row['resource-id']?.endsWith('/content_text_view'))?.['content-desc']||''};}
async function settleImport(kind){
 let rows=await ui();const folder=rows.find(row=>row['resource-id']==='android:id/button2'&&/APP DEFAULT FOLDER|默认|私有/.test(row.text));if(folder)await tap(folder);
 rows=await ui();if(rows.some(row=>row.text==='Screen touch control'))await adb('shell','input','tap','990','126');
 await until(async()=>{const book=await record(kind);return book?.totalChapterNum>0&&book;},'Legado parses '+kind,60000);
 await until(async()=>(await adb('shell','dumpsys','activity','activities')).split('\n').some(line=>/topResumedActivity|Resumed:/.test(line)&&line.includes('io.legado.app.ui.book.read.ReadBookActivity')),'real native reading activity');
}
async function send(kind){
 await adb('shell','am','start','-W','-f','0x20020000','-n','com.bookpack.mobile/.MainActivity');
 const evaluate=await connect();await until(()=>evaluate('!!window.BookpackLegado&&!!window.BookpackReader'),'Bookpack reader controls');
 await evaluate(`window.__handoffResult=null;window.__savedLegadoResult=window.onLegadoResult;window.onLegadoResult=result=>{window.__handoffResult=result;window.__savedLegadoResult(result)};BookpackLegado.choose('legado');BookpackReader.open(${JSON.stringify(state.books[kind].id)});true`);
 const result=await until(()=>evaluate('window.__handoffResult'),'native handoff result');assert.equal(result.ok,true,JSON.stringify(result));
 await evaluate('window.onLegadoResult=window.__savedLegadoResult');
 await until(async()=>(await adb('shell','dumpsys','activity','activities')).split('\n').some(line=>/topResumedActivity|Resumed:/.test(line)&&line.includes(packageName)),'Legado is foreground');
 await settleImport(kind);
 return result;
}

try{
 await settleImport('txt');let initial=await record('txt');
 // Normalize only this original test book to its first page.
 for(let count=0;count<20&&(initial.durChapterIndex||initial.durChapterPos);count++){
   const before=position(initial);await adb('shell','input','keyevent','24');await until(async()=>JSON.stringify(position(await record('txt')))!==JSON.stringify(before),'prior page');initial=await record('txt');
 }
 assert.deepEqual(position(initial),[0,0]);const firstPage=await page();assert.ok(firstPage.content.includes('邮差沿着山路走来'));
 state.checks.push('real TXT body rendered in Legado');await screenshot('安卓0.3-Legado-TXT正文.png');
 await adb('shell','input','keyevent','25');const next=await until(async()=>{const row=await record('txt');return row.durChapterPos>0&&row;},'volume down advances page');const nextPage=await page();assert.notEqual(nextPage.footer,firstPage.footer);await screenshot('安卓0.3-Legado音量下一页.png');
 await adb('shell','input','keyevent','24');await until(async()=>JSON.stringify(position(await record('txt')))==='[0,0]','volume up returns to prior page');const priorPage=await page();assert.equal(priorPage.footer,firstPage.footer);
 state.volumeKeys={first:firstPage.footer,next:nextPage.footer,prior:priorPage.footer,firstPosition:position(initial),nextPosition:position(next)};state.checks.push('volume down moves to next page','volume up moves to previous page');
 await adb('shell','input','keyevent','25');await until(async()=>(await record('txt')).durChapterPos>0,'reading position advances');const saved=await record('txt'),savedPage=await page();
 await adb('shell','am','force-stop','com.bookpack.mobile');
 assert.equal((await send('txt')).resumed,true);await until(async()=>(await page()).footer===savedPage.footer,'TXT page resumes after Bookpack handoff');const reopened=await record('txt');assert.deepEqual(position(reopened),position(saved));assert.equal((await books()).filter(book=>book.name.includes(state.books.txt.title)).length,1);
 state.txtResume={before:position(saved),after:position(reopened),footer:savedPage.footer,bookUrl:reopened.bookUrl};state.checks.push('repeat TXT handoff after Bookpack process restart resumes same book without duplicate');
 console.log('TXT, volume keys and repeated handoff verified.');
 assert.equal((await send('epub')).resumed,false);const epubFirst=await page();assert.ok(epubFirst.content.includes('原创 EPUB 联调样文'));await screenshot('安卓0.3-Legado-EPUB插图.png');
 let epub=await record('epub');for(let count=0;count<8&&epub.durChapterIndex<1;count++){const before=position(epub);await adb('shell','input','keyevent','25');await until(async()=>JSON.stringify(position(await record('epub')))!==JSON.stringify(before),'EPUB next page');epub=await record('epub');}
 assert.equal(epub.durChapterIndex,1);const epubSaved=await page();assert.ok(epubSaved.content.includes('潮声停下后'));await screenshot('安卓0.3-Legado-EPUB第二章.png');
 await adb('shell','am','force-stop','com.bookpack.mobile');await adb('shell','am','force-stop',packageName);
 assert.equal((await send('epub')).resumed,true);await until(async()=>(await page()).content.includes('潮声停下后'),'EPUB chapter resumes');const epubReopened=await record('epub');assert.deepEqual(position(epubReopened),position(epub));assert.equal((await books()).filter(book=>book.name.includes(state.books.epub.title)).length,1);
 await adb('shell','am','force-stop','com.bookpack.mobile');await adb('shell','am','force-stop',packageName);
 assert.equal((await send('epub')).resumed,true);await until(async()=>(await page()).content.includes('潮声停下后'),'persisted book mapping resumes EPUB');assert.equal((await books()).filter(book=>book.name.includes(state.books.epub.title)).length,1);
 state.epubResume={before:position(epub),after:position(epubReopened),bookUrl:epubReopened.bookUrl,chapters:epubReopened.totalChapterNum};state.checks.push('real EPUB body and chapter navigation in Legado','repeat EPUB handoff after both apps restart resumes chapter without duplicate','persisted EPUB book mapping survives another restart');
 await adb('shell','am','start','-W','-f','0x20020000','-n','com.bookpack.mobile/.MainActivity');let evaluate=await connect();assert.equal(await evaluate('BookpackAppearance.get().readerEngine'),'legado');disconnect();
 await adb('shell','am','force-stop','com.bookpack.mobile');await adb('shell','am','start','-W','-n','com.bookpack.mobile/.MainActivity');evaluate=await connect();await until(()=>evaluate('!!window.BookpackLegado'),'restart with saved reader preference');assert.equal(await evaluate('BookpackAppearance.get().readerEngine'),'legado');assert.equal((await evaluate('BookpackLegado.refresh()')).available,true);state.checks.push('reader choice survives Bookpack process restart');
 // Hide only the task-owned reader package temporarily; keep its books and settings.
 try{
   await adb('shell','pm','disable-user','--user','0',packageName);assert.equal((await evaluate('BookpackLegado.refresh()')).available,false);
   await evaluate(`BookpackReader.open(${JSON.stringify(state.books.txt.id)});true`);await until(()=>evaluate('document.getElementById("legado-dialog").open'),'missing Legado dialog');
   assert.ok(await evaluate('document.getElementById("legado-dialog-message").textContent.includes("还没有安装")'));await screenshot('安卓0.3-Legado未安装.png');
   await evaluate('document.getElementById("legado-use-builtin").click()');await until(()=>evaluate('!document.getElementById("reader-view").hidden&&!document.getElementById("reader-bookmark").disabled'),'actual explicit built-in fallback');
   await evaluate('document.getElementById("reader-back").click()');await until(()=>evaluate('document.getElementById("reader-view").hidden'),'close fallback');state.checks.push('missing Legado dialog on final APK','explicit built-in fallback on final APK');
 }finally{await adb('shell','pm','enable',packageName);await evaluate('BookpackLegado.choose("legado")');}
 assert.equal((await evaluate('BookpackLegado.refresh()')).available,true);
 state.phase='passed';state.scope='Android 15 / API 35 x86_64 emulator, real upstream Legado 3.26101819; no physical ARM phone connected.';fs.writeFileSync(path.join(out,'legado-native-validation.json'),JSON.stringify(state,null,2));console.log(JSON.stringify(state));
}catch(error){fs.writeFileSync(path.join(out,'legado-handoff-failure.log'),await adb('logcat','-d','-s','AndroidRuntime','Bookpack','python.stderr'));await screenshot('安卓0.3-Legado检查现场.png');throw error;}
finally{disconnect();}
