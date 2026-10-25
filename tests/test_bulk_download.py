"""Whole-book acquisition, missing-chapter fallback and source TXT layouts."""
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
from bookpack_bulk import match_sections, parse_sections
from bookpack_content import read_checkpoint, write_checkpoint
from bookpack_core import Engine
from bookpack_search import normalize, parse_titles
from bookpack_sources import SourceCancelled

TITLE = "雨后图书馆的来信"
AUTHOR = "原创作者"


class BulkFixture(BaseHTTPRequestHandler):
    calls = Counter()
    mode = "zip"

    def log_message(self, *args):
        pass

    def respond(self, raw, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.calls[path] += 1
        if path == "/search":
            return self.respond(f'<a href="/book">{TITLE}</a>'.encode(), "text/html; charset=utf-8")
        if path == "/book":
            href = "/down/1/" if self.mode == "landing" else "/whole.zip"
            indexes = [1, 3, 4] if self.mode == "extra" else range(1, 5)
            toc = ''.join(f'<a href="/chapter/{i}">{i}.第{i}章 回信{i}</a>' for i in indexes)
            html = f'<h1>{TITLE}</h1><p>作者：{AUTHOR}</p><a href="{href}">全集TXT下载</a><div class="toc">{toc}</div>'
            return self.respond(html.encode(), "text/html; charset=utf-8")
        if path == "/down/1/":
            return self.respond('<a href="/packdown.php?id=1&amp;type=txt&amp;fname=雨后图书馆的来信">全部TXT下载</a>'.encode(), "text/html; charset=utf-8")
        if path in {"/whole.zip", "/packdown.php"}:
            if self.mode == "404":
                return self.send_error(404)
            if self.mode == "html":
                return self.respond(b'<html><body>download error</body></html>', "text/html")
            indexes = [1, 3, 4] if self.mode == "missing" else range(1, 5)
            text = TITLE + '\n' + ''.join(f'\n{i}.第{i}章 回信{i}\n　　原创第{i}章正文：值班员收到第{i}封来信。\n' for i in indexes)
            if self.mode == "landing":
                return self.respond(text.encode('gb18030'), "application/octet-stream")
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('来信.txt', text.encode('gb18030'))
            return self.respond(stream.getvalue(), "application/zip")
        if path.startswith('/chapter/'):
            i = int(path.rsplit('/', 1)[-1])
            return self.respond(f'<div id="content">原创第{i}章正文：值班员收到第{i}封来信。</div>'.encode(), "text/html; charset=utf-8")
        self.send_error(404)


class BulkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), BulkFixture)
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
        self.engine = Engine(self.home)
        self.source = {'id':'bulk-fixture', 'name':'原创批量样文', 'url':self.base,
                       'search':{'url':'/search','params':{'q':'{title}'}},
                       'chapter_selector':'.toc a','content_selector':'#content', 'bulk_numbered_toc':True}
        self.engine.search.save_settings({'local_dirs':[], 'wikisource':False, 'books':[], 'web_sources':[self.source]})
        BulkFixture.mode = 'zip'
        BulkFixture.calls.clear()

    def tearDown(self):
        self.engine.cancel_event.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=10)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def download(self, mode='zip'):
        BulkFixture.mode = mode
        self.engine.create(parse_titles(TITLE))
        self.engine.runner.join(timeout=10)
        self.assertFalse(self.engine.runner.is_alive())
        batch = self.engine.snapshot()['batches'][0]
        task = batch['tasks'][0]
        self.assertEqual(task['status'], 'done', task['error'])
        self.assertEqual(task['chapter_count'], 4)
        self.assertEqual(task['expected_chapters'], 4)
        directory = self.engine.output / batch['directory']
        text = (directory / '书籍' / task['filename']).read_text(encoding='utf-8')
        for i in range(1, 5):
            self.assertEqual(text.count(f'原创第{i}章正文'), 1)
        positions = [text.index(f'原创第{i}章正文') for i in range(1, 5)]
        self.assertEqual(positions, sorted(positions))
        report = json.loads((directory / '下载报告.json').read_text(encoding='utf-8'))['books'][0]
        self.assertEqual(report['bulk_chapters'], task['bulk_chapters'])
        with zipfile.ZipFile(directory / batch['archive']) as archive:
            files = [name for name in archive.namelist() if name.endswith('.txt')]
            self.assertEqual(len(files), 1)
            self.assertEqual(archive.read(files[0]).decode('utf-8'), text)
        return batch, task

    def test_zip_uses_one_body_request_and_reuses_checkpoints(self):
        batch, task = self.download()
        self.assertEqual(BulkFixture.calls['/whole.zip'], 1)
        self.assertEqual(sum(value for key, value in BulkFixture.calls.items() if key.startswith('/chapter/')), 0)
        self.assertEqual(task['bulk_chapters'], 4)
        self.assertEqual(task['network_chapters'], 0)
        self.assertEqual(task['resumed_chapters'], 0)
        self.assertEqual(task['page_count'], 1)
        self.assertEqual(task['acquisition_method'], 'source_text_archive')
        cache = self.engine.output / batch['directory'] / '章节缓存' / task['id']
        self.assertEqual(len(list(cache.glob('整本正文-*.jsonl'))), 1)
        self.assertEqual(len(list(cache.glob('*.json'))), 1)
        calls = BulkFixture.calls.copy()
        self.download()
        self.assertEqual(BulkFixture.calls, calls)
        self.assertEqual(self.engine.snapshot()['batches'][0]['tasks'][0]['resumed_chapters'], 4)

    def test_txt_landing_and_php_link_are_discovered(self):
        _, task = self.download('landing')
        self.assertEqual(BulkFixture.calls['/down/1/'], 1)
        self.assertEqual(BulkFixture.calls['/packdown.php'], 1)
        self.assertEqual(task['acquisition_method'], 'source_text_file')
        self.assertEqual(task['network_chapters'], 0)

    def test_partial_whole_book_fetches_only_missing_chapter(self):
        _, task = self.download('missing')
        self.assertEqual(task['bulk_chapters'], 3)
        self.assertEqual(task['network_chapters'], 1)
        self.assertEqual(BulkFixture.calls['/chapter/2'], 1)
        self.assertEqual(sum(value for key, value in BulkFixture.calls.items() if key.startswith('/chapter/')), 1)
        self.assertEqual(task['acquisition_method'], 'source_bulk_and_chapters')

    def test_zip_retains_numbered_chapter_missing_from_web_toc(self):
        batch, task = self.download('extra')
        self.assertEqual(task['bulk_added_chapters'], 1)
        self.assertEqual(task['bulk_chapters'], 4)
        self.assertEqual(task['network_chapters'], 0)
        self.assertTrue(task['chapters'][1]['bulk_only'])
        calls = BulkFixture.calls.copy()
        # Model a healthy legacy task whose missing TOC entry was repaired into ordinary JSON checkpoints.
        cache = self.engine.output / batch['directory'] / '章节缓存' / task['id']
        for chapter in task['chapters']:
            row = read_checkpoint(cache, chapter)
            write_checkpoint(cache, chapter, row['text'], row['pages'])
        (cache / '整本索引.json').unlink()
        self.engine.batches[0]['tasks'][0]['chapters'][1].pop('bulk_only')
        _, repeated = self.download('extra')
        self.assertEqual(BulkFixture.calls, calls)
        self.assertEqual(repeated['resumed_chapters'], 4)
        self.assertIn('第2章', repeated['chapters'][1]['title'])

    def test_dead_or_html_file_falls_back_once_without_stalling_book(self):
        for mode in ['404', 'html']:
            with self.subTest(mode=mode):
                BulkFixture.calls.clear()
                self.engine.search.sources.lookup_cache.clear()
                # Different source identity prevents cross-batch chapter-cache reuse in this fallback check.
                self.source['id'] = 'bulk-fixture-' + mode
                self.engine.search.save_settings({'local_dirs':[], 'wikisource':False, 'books':[], 'web_sources':[self.source]})
                _, task = self.download(mode)
                self.assertEqual(BulkFixture.calls['/whole.zip'], 1)
                self.assertEqual(task['bulk_chapters'], 0)
                self.assertEqual(task['network_chapters'], 4)
                self.assertEqual(task['bulk_attempts'][-1]['state'], 'failed')

    def test_stop_during_import_keeps_valid_chapters_for_resume(self):
        manager = self.engine.search.sources
        task = manager.lookup(self.source, TITLE, AUTHOR, threading.Event())
        stopped = threading.Event()
        from unittest.mock import patch
        from bookpack_sources import write_checkpoint as original
        def write(*args, **kwargs):
            row = original(*args, **kwargs)
            stopped.set()
            return row
        cache = self.home / 'cache'
        with patch('bookpack_sources.write_checkpoint', side_effect=write):
            with self.assertRaises(SourceCancelled):
                manager.prefetch(task, stopped, lambda *args:None, cache)
        self.assertIsNotNone(read_checkpoint(cache, task['chapters'][0]))
        stats = manager.prefetch(task, threading.Event(), lambda *args:None, cache)
        self.assertEqual(stats['bulk_chapters'], 3)
        self.assertEqual(stats['resumed_chapters'], 1)
        self.assertEqual(stats['network_chapters'], 0)

    def test_legacy_saved_toc_discovers_bulk_when_resumed(self):
        manager = self.engine.search.sources
        task = manager.lookup(self.source, TITLE, AUTHOR, threading.Event())
        task.pop('bulk_downloads')
        task.pop('bulk_pages')
        stats = manager.prefetch(task, threading.Event(), lambda *args:None, self.home / 'legacy-cache')
        self.assertEqual(stats['bulk_chapters'], 4)
        self.assertEqual(stats['network_chapters'], 0)

    def test_separator_layout_matches_full_titles_and_discards_volume_headers(self):
        chapters = [{'title':'第1章 回信', 'url':'/1'}, {'title':'完本感言。', 'url':'/2'}]
        text = '------------\n\n第一卷：雨声\n\n------------\n\n第1章 回信\n\n原创正文。\n\n------------\n\n完本感言。\n\n原创感言。'
        sections = parse_sections(text, chapters, normalize)
        matched, _, added = match_sections(sections, chapters, normalize, 'http://example.test/book.txt')
        self.assertEqual(set(matched), {'/1', '/2'})
        self.assertEqual(matched['/1']['text'], '原创正文。')
        self.assertEqual(added, 0)


if __name__ == '__main__':
    unittest.main()
