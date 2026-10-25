"""Bounded chapter concurrency, cache reuse and compact progress with original text."""
import gzip
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_content import read_checkpoint
from bookpack_core import Engine
from bookpack_search import parse_titles
from bookpack_sources import SourceCancelled

TITLE = "雨后图书馆的十二封信"


class SpeedFixture(BaseHTTPRequestHandler):
    calls = Counter()
    lock = threading.Lock()
    active = peak = 0
    request_times = []

    def log_message(self, *args):
        pass

    def respond(self, text):
        raw = gzip.compress(text.encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        with self.lock:
            self.calls[path] += 1
        if path == "/search":
            return self.respond(f'<a href="/book">{TITLE}</a>')
        if path == "/book":
            toc = ''.join(f'<a href="/chapters/{index}.html">第{index}章 回信{index}</a>' for index in range(1, 13))
            return self.respond(f'<h1>{TITLE}</h1><p>作者：原创作者</p><div class="toc">{toc}</div>')
        if path.startswith("/chapters/"):
            index = int(path.rsplit('/', 1)[-1].split('.')[0])
            with self.lock:
                self.request_times.append(time.perf_counter())
                type(self).active += 1
                type(self).peak = max(self.peak, self.active)
            try:
                time.sleep(.04 * (1 + index % 3))
                return self.respond(f'<div id="content">原创第{index}章正文：值班员将第{index}封回信放进旧木箱。</div>')
            finally:
                with self.lock:
                    type(self).active -= 1
        self.send_error(404)


class SpeedTests(unittest.TestCase):
    measurements = {}

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SpeedFixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.root = Path(__file__).resolve().parents[1] / ".functional-runs"
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.home = Path(self.temp.name).resolve()
        assert self.home.is_relative_to(self.root.resolve())
        self.engine = Engine(self.home)
        self.manager = self.engine.search.sources
        self.source = {"id":"original-speed", "name":"原创样文", "url":self.base, "enabled":True,
                       "search":{"url":"/search", "params":{"q":"{title}"}},
                       "chapter_selector":".toc a", "content_selector":"#content"}
        self.engine.search.save_settings({"local_dirs":[], "wikisource":False, "books":[], "web_sources":[self.source]})
        with SpeedFixture.lock:
            SpeedFixture.calls.clear()
            SpeedFixture.active = SpeedFixture.peak = 0
            SpeedFixture.request_times = []

    def tearDown(self):
        self.engine.cancel_event.set()
        if self.engine.runner:
            self.engine.runner.join(timeout=10)
        self.engine.suggestions.close()
        self.temp.cleanup()

    def book(self):
        return self.manager.lookup(self.source, TITLE, "原创作者", threading.Event())

    def finish(self):
        self.engine.runner.join(timeout=10)
        self.assertFalse(self.engine.runner.is_alive())
        batch = self.engine.snapshot()["batches"][0]
        self.assertEqual(batch["status"], "complete")
        return batch

    def test_parallel_download_preserves_order_and_records_speed(self):
        task = self.book()
        start = time.perf_counter()
        self.manager.export(task, self.home / 'serial.txt', threading.Event(), lambda *args:None,
                            cache_dir=self.home / 'serial-cache')
        serial = time.perf_counter() - start
        cache = self.home / 'parallel-cache'
        start = time.perf_counter()
        parallel_started = start
        stats = self.manager.prefetch(task, threading.Event(), lambda *args:None, cache, workers=6)
        prefetched_at = time.perf_counter()
        self.manager.export(task, self.home / 'parallel.txt', threading.Event(), lambda *args:None, cache_dir=cache)
        parallel = time.perf_counter() - start
        starts = [round((value - start) * 1000, 1) for value in SpeedFixture.request_times[12:]]
        self.assertEqual((self.home / 'serial.txt').read_bytes(), (self.home / 'parallel.txt').read_bytes())
        self.assertEqual(stats, {"resumed_chapters":0, "network_chapters":12})
        self.assertGreater(SpeedFixture.peak, 1)
        self.assertLessEqual(SpeedFixture.peak, 6)
        calls = SpeedFixture.calls.copy()
        start = time.perf_counter()
        warm = self.manager.prefetch(task, threading.Event(), lambda *args:None, cache, workers=6)
        self.manager.export(task, self.home / 'warm.txt', threading.Event(), lambda *args:None, cache_dir=cache)
        reused = time.perf_counter() - start
        self.assertEqual(warm, {"resumed_chapters":12, "network_chapters":0})
        self.assertEqual(SpeedFixture.calls, calls)
        self.measurements.update(chapters=12, serial_seconds=round(serial, 4), parallel_seconds=round(parallel, 4),
                                 cache_seconds=round(reused, 4), speedup=round(serial / parallel, 2), peak_requests=SpeedFixture.peak,
                                 prefetch_seconds=round(prefetched_at-parallel_started,4), parallel_request_starts_ms=starts)

    def test_repeated_batch_reuses_valid_chapters_and_search_metadata(self):
        self.engine.create(parse_titles(TITLE))
        self.finish()
        calls = SpeedFixture.calls.copy()
        self.engine.create(parse_titles(TITLE))
        batch = self.finish()
        task = batch['tasks'][0]
        self.assertEqual(SpeedFixture.calls, calls)
        self.assertEqual((task['resumed_chapters'], task['network_chapters']), (12, 0))
        full = self.engine.snapshot()
        compact = self.engine.snapshot(compact=True)
        visible = compact['batches'][0]['tasks'][0]
        for key in ['id', 'title', 'status', 'chapter_count', 'expected_chapters', 'candidates']:
            self.assertEqual(visible[key], full['batches'][0]['tasks'][0][key])
        self.assertNotIn('chapters', visible)
        self.assertNotIn('source_pages', visible)
        self.assertEqual(len(self.engine.snapshot()['batches'][0]['tasks'][0]['chapters']), 12)

    def test_lookup_cache_preserves_candidates_and_caller_changes_do_not_leak(self):
        task = self.book()
        task['chapters'][0]['title'] = '调用者修改'
        again = self.book()
        self.assertEqual(again['chapters'][0]['title'], '第1章 回信1')
        self.assertEqual(SpeedFixture.calls['/book'], 1)
        rows, cached = [], []
        self.assertIsNone(self.manager.lookup(self.source, '雨后', '', threading.Event(), rows))
        calls = SpeedFixture.calls.copy()
        self.assertIsNone(self.manager.lookup(self.source, '雨后', '', threading.Event(), cached))
        self.assertTrue(rows)
        self.assertEqual(rows, cached)
        self.assertEqual(SpeedFixture.calls, calls)

    def test_parallel_cancel_keeps_completed_chapters_for_resume(self):
        task = self.book()
        cache, stop = self.home / 'cancel-cache', threading.Event()
        with self.assertRaises(SourceCancelled):
            self.manager.prefetch(task, stop, lambda *args:stop.set(), cache, workers=6)
        complete = [chapter for chapter in task['chapters'] if read_checkpoint(cache, chapter)]
        self.assertTrue(complete)
        calls = SpeedFixture.calls.copy()
        stop.clear()
        stats = self.manager.prefetch(task, stop, lambda *args:None, cache, workers=6)
        self.assertGreaterEqual(stats['resumed_chapters'], len(complete))
        for chapter in complete:
            path = urllib.parse.urlsplit(chapter['url']).path
            self.assertEqual(SpeedFixture.calls[path], calls[path])

    def test_missing_chapter_does_not_repeat_permanent_404(self):
        task = self.book()
        chapter = {'title':'第13章 回信13', 'url':self.base+'/missing/13.html'}
        with self.assertRaises(OSError):
            self.manager.chapter(task, chapter, threading.Event())
        self.assertEqual(SpeedFixture.calls['/missing/13.html'], 1)


if __name__ == '__main__':
    unittest.main()
