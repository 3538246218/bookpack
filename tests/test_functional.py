import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DEMO_TEXT, demo_epub
from bookpack_core import Engine, parse_tasks
from bookpack_search import SearchError, SearchService, parse_titles


class FixtureHandler(BaseHTTPRequestHandler):
    calls = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.calls[self.path] = self.calls.get(self.path, 0) + 1
        if self.path.startswith("/wiki-api?"):
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if query["action"] == "query":
                data = {"query": {"pages": {"1": {"pageid": 1, "ns": 0, "title": "原创故事", "fullurl": "https://zh.wikisource.org/wiki/原创故事"}}}}
            else:
                pages = {"原创故事": '<div id="headerContainer">导航不应导出</div><p>原创目录</p><a href="/wiki/原创故事/上">上篇</a><a href="/wiki/原创故事/下">下篇</a><a href="/wiki/无关作品">无关</a>',
                         "原创故事/上": '<p>第一篇原创正文。</p><a href="/wiki/原创故事/下">下篇</a>',
                         "原创故事/下": '<p>第二篇原创正文。</p><a href="/wiki/原创故事/上">上篇</a><div class="licenseContainer">许可模板界面</div>'}
                data = {"parse": {"text": {"*": pages[query["page"]]}}}
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/temporary.txt" and self.calls[self.path] == 1:
            self.send_error(503)
            return
        if self.path == "/epub":
            data, mime = demo_epub(), "application/epub+zip"
        elif self.path == "/login.html":
            data, mime = "<!doctype html><html><body>需要登录</body></html>".encode(), "text/html; charset=utf-8"
        elif self.path == "/error.json":
            data, mime = b'{"error":"file not found"}', "application/json"
        elif self.path == "/gbk.txt":
            data, mime = DEMO_TEXT.encode("gb18030"), "text/plain; charset=gb18030"
        elif self.path == "/slow.txt":
            data, mime = (DEMO_TEXT * 180).encode(), "text/plain; charset=utf-8"
        else:
            data, mime = DEMO_TEXT.encode(), "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            if self.path == "/slow.txt":
                for start in range(0, len(data), 4096):
                    self.wfile.write(data[start:start + 4096])
                    self.wfile.flush()
                    time.sleep(.035)
            else:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.04)
    raise AssertionError("功能测试超时")


class FunctionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.temporary_root = Path(__file__).resolve().parents[1] / ".functional-runs"
        cls.temporary_root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.temporary_root)
        self.home = Path(self.temporary.name).resolve()
        assert self.home.is_relative_to(self.temporary_root.resolve())
        self.engine = Engine(self.home)
        self.engine.search.config["web_sources"] = []
        FixtureHandler.calls.clear()

    def tearDown(self):
        if self.engine.runner:
            self.engine.cancel_event.set()
            self.engine.runner.join(timeout=18)
        self.temporary.cleanup()

    def finish(self, engine=None):
        engine = engine or self.engine
        engine.runner.join(timeout=12)
        self.assertFalse(engine.runner.is_alive())
        return engine.snapshot()["batches"][0]

    def test_import_csv_json_and_deduplicate(self):
        tasks = parse_tasks(f'书名,下载链接\n"雨后，借书处",{self.origin}/utf8.txt\n"雨后，借书处",{self.origin}/utf8.txt')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "雨后，借书处")
        self.assertEqual(parse_tasks(json.dumps({"books": [{"url": self.origin + "/book.epub"}]}))[0]["title"], "book")

    def test_title_import_local_lookup_and_not_found_report(self):
        library = self.home / "本地书库"
        library.mkdir()
        (library / "雨后，借书处_全本.txt").write_text(DEMO_TEXT, encoding="utf-8")
        raw = library / "batch5_raw"
        raw.mkdir()
        (raw / "没有这本书.txt").write_text("不应选择中间文件", encoding="utf-8")
        self.engine.search.save_settings({"local_dirs": [str(library)], "wikisource": False})
        tasks = parse_titles('雨后，借书处\n《雨后，借书处》\n没有这本书')
        self.assertEqual(len(tasks), 2)
        self.assertEqual(parse_titles('title,author\n"雨后，借书处",作者')[0]["title"], "雨后，借书处")
        self.assertEqual(parse_titles('狂人日记 | 鲁迅')[0]["author"], "鲁迅")
        self.engine.create(tasks)
        batch = self.finish()
        self.assertEqual([task["status"] for task in batch["tasks"]], ["done", "failed"])
        self.assertIn("未找到", batch["tasks"][1]["error"])
        with zipfile.ZipFile(self.engine.archive_path(batch["id"])) as archive:
            report = json.loads(archive.read("下载报告.json"))
            self.assertEqual(report["requested"], 2)
            self.assertEqual(report["books"][0]["search_source"], "本地书库")
            self.assertEqual(report["books"][1]["status"], "failed")
            self.assertEqual(report["books"][1]["search_source"], "未匹配")
            self.assertEqual(archive.read("书籍/" + batch["tasks"][0]["filename"]).decode(), DEMO_TEXT)
        (library / "没有这本书.txt").write_text(DEMO_TEXT, encoding="utf-8")
        self.engine.retry(batch["id"])
        resumed = self.finish()
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["tasks"][0]["sha256"], batch["tasks"][0]["sha256"])

    def test_catalog_alias_author_conflict_and_persistence(self):
        expected = hashlib.sha256(DEMO_TEXT.encode()).hexdigest()
        config = {"local_dirs": [], "wikisource": False, "books": [
            {"title": "同名作品", "author": "甲", "aliases": ["另一个书名"], "url": self.origin + "/utf8.txt", "sha256": expected},
            {"title": "同名作品", "author": "乙", "url": self.origin + "/gbk.txt"}]}
        self.engine.search.save_settings(config)
        restored = SearchService(self.home)
        self.assertEqual(restored.settings()["books"][0]["expected_sha256"], expected)
        with self.assertRaisesRegex(SearchError, "不同作者"):
            restored.resolve("同名作品", "", threading.Event())
        self.engine.create(parse_titles("另一个书名 | 甲\n同名作品 | 乙"))
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        self.assertEqual(batch["tasks"][0]["title"], "另一个书名")
        self.assertEqual(batch["tasks"][0]["matched_title"], "同名作品")
        self.assertEqual(batch["tasks"][1]["matched_author"], "乙")

    def test_wikisource_title_lookup_and_ordered_subpage_export(self):
        self.engine.search.save_settings({"local_dirs": [], "wikisource": True})
        self.engine.search.wiki_api = self.origin + "/wiki-api"
        self.engine.create(parse_titles("原创故事"))
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        task = batch["tasks"][0]
        self.assertEqual(task["source_pages"], ["原创故事", "原创故事/上", "原创故事/下"])
        self.assertEqual(task["page_count"], 3)
        text = (self.engine.batch_directory(batch["id"]) / "书籍" / task["filename"]).read_text(encoding="utf-8")
        self.assertLess(text.index("第一篇原创正文"), text.index("第二篇原创正文"))
        self.assertNotIn("导航不应导出", text)
        self.assertNotIn("许可模板界面", text)
        self.assertEqual(text.count("第一篇原创正文"), 1)

    def test_download_formats_failure_and_zip_report(self):
        tasks = parse_tasks([
            {"title": "UTF8原创", "url": self.origin + "/utf8.txt", "sha256": hashlib.sha256(DEMO_TEXT.encode()).hexdigest()},
            {"title": "GBK原创", "url": self.origin + "/gbk.txt"},
            {"title": "EPUB原创", "url": self.origin + "/epub"},
            {"title": "非文件链接", "url": self.origin + "/login.html"},
            {"title": "错误数据", "url": self.origin + "/error.json"},
        ])
        batch_id = self.engine.create(tasks, "功能验收")
        batch = self.finish()
        self.assertEqual(batch["status"], "partial")
        self.assertEqual([task["status"] for task in batch["tasks"]], ["done", "done", "done", "failed", "failed"])
        self.assertIn("登录", batch["tasks"][3]["error"])
        self.assertIn("JSON", batch["tasks"][4]["error"])
        self.assertEqual(batch["tasks"][1]["encoding"], "gb18030")
        with zipfile.ZipFile(self.engine.archive_path(batch_id)) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("书籍/")]), 3)
            for name in ["书籍/01 UTF8原创.txt", "书籍/02 GBK原创.txt"]:
                self.assertEqual(archive.read(name).decode("utf-8"), DEMO_TEXT)
            with zipfile.ZipFile(io.BytesIO(archive.read("书籍/03 EPUB原创.epub"))) as epub:
                self.assertIn("content.opf", epub.namelist())
            report = json.loads(archive.read("下载报告.json"))
            self.assertEqual((report["requested"], report["downloaded"], report["failed"]), (5, 3, 2))
            self.assertIn("失败原因", archive.read("下载报告.csv").decode("utf-8-sig"))

    def test_transient_failure_retries(self):
        self.engine.create(parse_tasks(f"自动重试 | {self.origin}/temporary.txt"))
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        self.assertEqual(batch["tasks"][0]["attempt"], 2)

    def test_cancel_resume_and_restore_completed_files(self):
        batch_id = self.engine.create(parse_tasks(f"已完成 | {self.origin}/utf8.txt\n未完成 | {self.origin}/slow.txt"), concurrency=2)
        wait_for(lambda: self.engine.snapshot()["batches"][0]["tasks"][0]["status"] == "done")
        self.engine.cancel(batch_id)
        cancelled = self.finish()
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["tasks"][1]["status"], "cancelled")
        self.engine = Engine(self.home)
        self.engine.retry(batch_id)
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        self.assertEqual(FixtureHandler.calls["/utf8.txt"], 1)
        self.assertEqual(len(batch["tasks"]), 2)
        with zipfile.ZipFile(self.engine.archive_path(batch_id)) as archive:
            report = json.loads(archive.read("下载报告.json"))
            self.assertEqual(report["downloaded"], 2)


if __name__ == "__main__":
    unittest.main()
