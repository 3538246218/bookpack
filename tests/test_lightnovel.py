"""Light-novel title lookup, offline completion and actual EPUB acquisition."""
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_search import SearchCandidates, parse_titles
from bookpack_lightnovel import catalog_entries

TITLE = "雨后图书馆与第十二封来信"
AUTHOR = "原创作者"
ALIAS = "海边来信集"


def original_epub(title=TITLE):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        archive.writestr('content.opf', f'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>{AUTHOR}</dc:creator></metadata><manifest><item id="text" href="text.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="text"/></spine></package>')
        archive.writestr('text.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一卷 第一章</h1><p>原创样文：值班员将雨后的第十二封来信放进旧木箱。</p></body></html>')
    return stream.getvalue()


class NovelFixture(BaseHTTPRequestHandler):
    calls = Counter()

    def log_message(self, *args):
        pass

    def respond(self, raw, mime):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.calls[path] += 1
        if path in {'/book.epub', '/wrong.epub'}:
            return self.respond(original_epub('另一部原创作品' if path == '/wrong.epub' else TITLE), 'application/octet-stream')
        if path == '/search':
            return self.respond(f'<a href="/book">{TITLE}</a>'.encode(), 'text/html; charset=utf-8')
        if path == '/book':
            return self.respond(f'<h1>{TITLE}</h1><p>作者：{AUTHOR}</p><a href="/fallback.txt">TXT下载</a>'.encode(), 'text/html; charset=utf-8')
        if path == '/fallback.txt':
            return self.respond('原创后备正文：雨后的来信。'.encode(), 'text/plain; charset=utf-8')
        return self.send_error(404)


class LightNovelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), NovelFixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.root = Path(__file__).resolve().parents[1] / '.functional-runs'
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.home = Path(self.temp.name)
        self.path = self.home / 'novels.json'
        self.books = [{'id':'1','title':TITLE,'author':AUTHOR,'aliases':[ALIAS], 'url':self.base+'/book.epub',
                       'source_page':self.base+'/entry/1','file_updated_at':'2025-10-01','work_updated_at':'2026-10-20'}]
        self.engine = Engine(self.home)
        self.source = {'id':'original-novel','name':'原创轻小说目录','url':self.base,
                       'kind':'lightnovel_catalog','catalog_file':str(self.path)}
        self.configure()
        NovelFixture.calls.clear()

    def configure(self, extra=()):
        self.path.write_text(json.dumps({'books':self.books},ensure_ascii=False),encoding='utf-8')
        self.engine.search.save_settings({'local_dirs':[], 'books':[], 'wikisource':False,
                                         'web_sources':[self.source,*extra]})
        self.engine.search.sources.lookup_cache.clear()

    def tearDown(self):
        self.engine.cancel_event.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=10)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def finish(self, query=TITLE):
        self.engine.create(parse_titles(query))
        self.engine.runner.join(timeout=10)
        self.assertFalse(self.engine.runner.is_alive())
        batch = self.engine.snapshot()['batches'][0]
        return batch, batch['tasks'][0]

    def test_exact_alias_author_and_ambiguous_same_title(self):
        found = self.engine.search.resolve(ALIAS, AUTHOR, threading.Event())
        self.assertEqual(found['matched_title'], TITLE)
        self.assertEqual(found['match_kind'], 'alias')
        self.assertEqual(found['download_kind'], 'source_file')
        self.assertFalse(NovelFixture.calls)
        self.assertIsNone(self.engine.search.sources.lookup(self.source,TITLE,'不同作者',threading.Event()))
        self.books.append({**self.books[0], 'id':'2', 'author':'另一位原创作者'})
        self.configure()
        with self.assertRaises(SearchCandidates) as caught:
            self.engine.search.resolve(TITLE, '', threading.Event())
        self.assertEqual(len(caught.exception.candidates),2)

    def test_offline_prefix_alias_and_typo_suggestions_do_not_download(self):
        for query in ['雨后图书馆', ALIAS, '雨后图书馆与第十二封来讯']:
            with self.subTest(query=query):
                result = self.engine.suggest(query, online=False)
                self.assertTrue(any(row['title']==TITLE for row in result['suggestions']))
        self.assertFalse(NovelFixture.calls)
        self.assertFalse(self.engine.snapshot()['batches'])

    def test_whole_epub_is_packaged_with_file_date_and_correct_platform_separation(self):
        batch, task = self.finish(ALIAS)
        self.assertEqual(task['status'],'done',task['error'])
        self.assertEqual(task['format'],'epub')
        self.assertEqual(NovelFixture.calls['/book.epub'],1)
        self.assertFalse(task['platform_verified'])
        self.assertNotEqual(task['platform_label'],self.source['name'])
        directory = self.engine.output / batch['directory']
        report=json.loads((directory/'下载报告.json').read_text(encoding='utf-8'))['books'][0]
        self.assertEqual(report['source_updated_at'],'2025-10-01')
        self.assertEqual(report['work_updated_at'],'2026-10-20')
        self.assertIn('可能未含后续更新',report['edition_note'])
        with zipfile.ZipFile(directory/batch['archive']) as archive:
            member=next(name for name in archive.namelist() if name.endswith('.epub'))
            self.assertEqual(archive.read(member),original_epub())

    def test_missing_or_wrong_title_epub_falls_back_to_other_source(self):
        backup={'id':'backup','name':'原创后备','url':self.base,'search':{'url':'/search','params':{'q':'{title}'}}}
        for file in ['missing.epub','wrong.epub']:
            with self.subTest(file=file):
                NovelFixture.calls.clear()
                self.books[0]['url']=self.base+'/'+file
                self.configure([backup])
                _,task=self.finish()
                self.assertEqual(task['status'],'done',task['error'])
                self.assertEqual(task['source_id'],'backup')
                self.assertEqual(task['format'],'txt')
                self.assertEqual(NovelFixture.calls['/'+file],1)
                self.assertTrue(task['source_failures'])
                self.assertFalse(task.get('lightnovel_id'))

    def test_metadata_only_entry_does_not_claim_downloadable_text(self):
        self.books[0]['url']=''
        self.configure()
        _,task=self.finish()
        self.assertEqual(task['status'],'needs_choice')
        self.assertEqual(task['candidates'][0]['download_kind'],'online_only')
        self.assertFalse(NovelFixture.calls)

    def test_public_catalog_fields_keep_file_and_work_update_distinct(self):
        rows=catalog_entries({'items':[{'n':'123','t':TITLE,'au':AUTHOR,'a':ALIAS+'/另一个别名',
                                        'x':'example/repo/main/原创 来信.epub','u':'2026-10-20','xu':'2025-10-01'}]})
        self.assertEqual(rows[0]['aliases'],[ALIAS,'另一个别名'])
        self.assertIn('%20',rows[0]['url'])
        self.assertEqual(rows[0]['file_updated_at'],'2025-10-01')
        self.assertEqual(rows[0]['work_updated_at'],'2026-10-20')


if __name__=='__main__':
    unittest.main()
