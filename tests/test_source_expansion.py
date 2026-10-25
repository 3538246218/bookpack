"""Multiple-source lookup, identity preference and complete paginated directories."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_content import exported_sections
from bookpack_search import normalize, parse_titles
from bookpack_sources import Document, SourceCancelled, SourceManager

TITLE = "雨后图书馆的第十二封来信"
AUTHOR = "原创作者"


class ExpansionFixture(BaseHTTPRequestHandler):
    calls = Counter()
    broken = False

    def log_message(self, *args):
        pass

    def respond(self, text, mime='text/html; charset=utf-8'):
        raw = text.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        self.calls[parsed.path] += 1
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == '/' and query.get('a') == 'search.json':
            return self.respond('search('+json.dumps({'content':f'<h3><a href="/book">{TITLE}</a></h3>'},ensure_ascii=False)+');', 'application/javascript; charset=utf-8')
        if parsed.path == '/search':
            return self.respond(f'<div class="results"><a href="/book">[科幻]{TITLE}</a></div>')
        if parsed.path == '/book':
            recent=''.join(f'<a href="/chapter/{i}.html">第{i}章 回信{i}</a>' for i in [8,7,6])
            return self.respond(f'<h1>站点标题</h1><div class="book-title"><h1>{TITLE}作者：{AUTHOR}</h1></div><span class="author">{AUTHOR}</span><a href="/toc/1.html">查看全部章节</a><div class="latest">{recent}</div><div class="latest toc-shortcut"><a href="/chapter/1.html">第1章 回信1</a></div>')
        if parsed.path.startswith('/toc/'):
            page = int(parsed.path.rsplit('/',1)[-1].split('.')[0])
            if self.broken and page == 2:
                return self.send_error(503)
            time.sleep(.06 if page == 2 else .01)
            toc=''.join(f'<a href="/chapter/{i}.html">第{i}章 回信{i}</a>' for i in range(page*2-1,page*2+1))
            # First links skip page two; it is discovered only after fetching the last-page shortcut.
            links=[1,3] if page == 1 else [1,2,3]
            pages=''.join(f'<a href="/toc/{i}.html">{i}</a>' for i in links)
            return self.respond(f'<div class="toc">{toc}</div><div class="pages">{pages}</div>')
        if parsed.path.startswith('/chapter/'):
            number = parsed.path.rsplit('/',1)[-1].split('.')[0]
            return self.respond(f'<div id="body">原创第{number}章正文：雨后收到第{number}封来信。</div>')
        self.send_error(404)


class ExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),ExpansionFixture)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
        cls.root=Path(__file__).resolve().parents[1]/'.functional-runs'
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=self.root)
        self.home=Path(self.temp.name)
        self.engine=Engine(self.home)
        self.manager=self.engine.search.sources
        self.source={'id':'page-fixture','name':'原创分页站','url':self.base,'enabled':True,
          'search':{'url':'/search','params':{'q':'{title}'}},'result_selector':'.results a','result_title_strip':r'^\[[^\]]*\]',
          'book_title_selector':'.book-title h1','book_title_pattern':r'^(.*?)作者[：:]','author_selector':'.author',
          'chapter_selector':'.toc a','include_all_toc_entries':True,'content_selector':'#body',
          'toc_page_selector':'.pages a','toc_page_pattern':r'/toc/(\d+)\.html$',
          'latest_selector':'.latest a','latest_exclude_selector':'.toc-shortcut a','latest_descending':True}
        self.engine.search.save_settings({'local_dirs':[],'books':[],'wikisource':False,'web_sources':[self.source]})
        ExpansionFixture.calls.clear()
        ExpansionFixture.broken=False

    def tearDown(self):
        self.engine.cancel_event.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=10)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def sources(self,count):
        return [{'id':str(i),'name':'原创来源'+str(i),'url':self.base,'enabled':True} for i in range(count)]

    def found(self,source,author=AUTHOR):
        return {'source_id':source['id'],'source':source['name'],'url':self.base+'/original.txt','local_path':'',
                'source_page':self.base+'/entry/'+source['id'],'matched_title':TITLE,'matched_author':author,
                'match_kind':'exact','match_score':1.0,'matched_on':TITLE,'edit_distance':0,'download_kind':'source_file'}

    def test_source_specific_search_labels_and_book_metadata(self):
        task=self.manager.lookup(self.source,TITLE,AUTHOR,threading.Event())
        self.assertEqual(task['matched_title'],TITLE)
        self.assertEqual(task['matched_author'],AUTHOR)
        self.assertEqual(len(task['chapters']),2)
        self.assertTrue(task['toc_pages'])
        self.assertEqual(len(task['recent_chapters']),3)
        self.assertEqual(task['recent_chapters'][-1]['title'],'第8章 回信8')

    def test_parallel_queries_keep_priority_and_bound_workers(self):
        sources=self.sources(8)
        active=peak=0
        lock=threading.Lock()
        def lookup(source,*args,**kwargs):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:
                time.sleep(.12 if source['id']=='0' else .04)
                return self.found(source)
            finally:
                with lock:active-=1
        with patch.object(self.manager,'lookup',side_effect=lookup):
            start=time.perf_counter()
            task=self.manager.find(sources,TITLE,AUTHOR,threading.Event(),[],[])
            elapsed=time.perf_counter()-start
        self.assertEqual(task['source_id'],'0')
        self.assertGreater(peak,1)
        self.assertLessEqual(peak,6)
        self.assertLess(elapsed,.8)

    def test_verified_author_preference_keeps_other_author_as_manual_candidate(self):
        sources=self.sources(2)
        def lookup(source,*args,**kwargs):
            time.sleep(.01 if source['id']=='0' else .05)
            return self.found(source,'另一位作者' if source['id']=='0' else AUTHOR)
        rows=[]
        with patch.object(self.manager,'lookup',side_effect=lookup):
            task=self.manager.find(sources,TITLE,'',threading.Event(),[],rows,preferred_author=AUTHOR)
        self.assertEqual(task['matched_author'],AUTHOR)
        other=next(row for row in rows if row['matched_author']=='另一位作者')
        self.assertEqual(other['match_kind'],'same_title')
        self.assertIsNone(self.manager.matcher.automatic(TITLE,[other]))

    def test_unknown_same_title_different_authors_require_choice(self):
        sources=self.sources(2)
        with patch.object(self.manager,'lookup',side_effect=lambda source,*a,**k:self.found(source,AUTHOR+source['id'])):
            rows=[]
            task=self.manager.find(sources,TITLE,'',threading.Event(),[],rows)
        self.assertIsNone(task)
        self.assertEqual(len({row['matched_author'] for row in rows}),2)
        self.assertIsNone(self.manager.matcher.automatic(TITLE,self.manager.matcher.rank(rows)))

    def test_timeout_returns_without_waiting_and_temporarily_skips_failed_source(self):
        self.manager.search_budget=.12
        calls=[]
        def lookup(source,title,author,cancelled,*args,**kwargs):
            calls.append(source['id'])
            cancelled.wait(1)
            self.manager.check(cancelled)
            return None
        sources=self.sources(1)
        with patch.object(self.manager,'lookup',side_effect=lookup):
            start=time.perf_counter()
            self.assertIsNone(self.manager.find(sources,TITLE,'',threading.Event(),[],[]))
            self.assertLess(time.perf_counter()-start,.8)
            self.assertFalse(self.manager.source_ready(sources[0]))
            self.manager.find(sources,TITLE,'',threading.Event(),[],[])
        self.assertEqual(calls,['0'])
        self.manager.retry_sources()
        self.assertTrue(self.manager.source_ready(sources[0]))

    def test_paginated_toc_orders_pages_and_appends_verified_recent_chapters(self):
        self.engine.create(parse_titles(TITLE+' | '+AUTHOR))
        self.engine.runner.join(timeout=10)
        self.assertFalse(self.engine.runner.is_alive())
        batch=self.engine.snapshot()['batches'][0];task=batch['tasks'][0]
        self.assertEqual(task['status'],'done',task['error'])
        self.assertEqual(task['chapter_count'],8)
        self.assertEqual(task['expected_chapters'],8)
        text=(self.engine.output/batch['directory']/'书籍'/task['filename']).read_text(encoding='utf-8')
        sections=exported_sections(task,text)
        self.assertEqual([chapter['title'] for chapter,body in sections],[f'第{i}章 回信{i}' for i in range(1,9)])
        for i,(chapter,body) in enumerate(sections,1):self.assertIn(f'原创第{i}章正文',body)
        self.assertEqual(ExpansionFixture.calls['/toc/2.html'],1)
        self.assertEqual(ExpansionFixture.calls['/toc/3.html'],1)
        # The complete directory cache does not lose the two recent chapters on reuse.
        initial=self.manager.lookup(self.source,TITLE,AUTHOR,threading.Event())
        calls=ExpansionFixture.calls.copy()
        self.manager.complete_toc(initial,threading.Event())
        self.assertEqual(len(initial['chapters']),8)
        self.assertEqual(ExpansionFixture.calls,calls)

    def test_missing_toc_page_is_failure_instead_of_partial_success(self):
        ExpansionFixture.broken=True
        self.engine.create(parse_titles(TITLE+' | '+AUTHOR))
        self.engine.runner.join(timeout=10)
        self.assertFalse(self.engine.runner.is_alive())
        batch=self.engine.snapshot()['batches'][0];task=batch['tasks'][0]
        self.assertEqual(task['status'],'failed')
        self.assertFalse(list((self.engine.output/batch['directory']/'书籍').glob('*.txt')))
        self.assertTrue(task['source_failures'])

    def test_jsonp_search_and_mirror_fallback_use_real_http_interfaces(self):
        source={**self.source,'kind':'quanben','result_selector':'h3 a','result_title_strip':''}
        task=self.manager.lookup(source,TITLE,AUTHOR,threading.Event())
        self.assertEqual(task['matched_title'],TITLE)
        mirror={**self.source,'id':'mirror','url':'http://127.0.0.1:1/','mirrors':[self.base]}
        attempts=[]
        task=self.manager.find([mirror],TITLE,AUTHOR,threading.Event(),attempts,[])
        self.assertEqual(task['source_id'],'mirror')
        self.assertTrue(task['source_page'].startswith(self.base))
        self.assertTrue(attempts[0]['mirror_errors'])


if __name__=='__main__':
    unittest.main()
