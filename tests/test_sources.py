import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_core import Engine
from bookpack_search import parse_titles
from bookpack_sources import SourceError, validate_sources

ORIGINAL = "原创功能样文：雨停之后，值班员收起门外的伞。\n"


class SourceFixture(BaseHTTPRequestHandler):
    posted = []

    def log_message(self, *args):
        pass

    def respond(self, value, mime="text/html; charset=utf-8"):
        raw = value.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("ascii")
        self.posted.append(dict(urllib.parse.parse_qsl(body, encoding="gb18030")))
        self.respond('<div class="bk"><h3><a href="/book">雨后借书处</a></h3></div>')

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            return self.respond('<form method="get" action="/search"><input name="keyword"><input type="hidden" name="scope" value="books"></form>')
        if path == "/search":
            return self.respond('<a href="/book">雨后借书处</a>')
        if path == "/book":
            return self.respond('<h1>雨后借书处</h1><div>作者：原创作者</div><a href="/chapter/2">第二章 回信</a><ul class="toc"><li><a href="/chapter/1">第一章 雨声</a></li></ul><a href="/all">全部章节</a>')
        if path == "/all":
            return self.respond('<ul class="toc"><li><a href="/chapter/1">第一章 雨声</a></li><li><a href="/chapter/2">第二章 回信</a></li></ul>')
        if path == "/chapter/1":
            return self.respond('<h1>第一章 雨声</h1><div id="content"><p>原创第一章上半。</p></div><a href="/chapter/1_2">下一页</a>')
        if path == "/chapter/1_2":
            return self.respond('<div id="content"><p>原创第一章下半。</p></div><a href="/chapter/2">下一章</a>')
        if path == "/chapter/2":
            return self.respond('<div id="content"><p>原创第二章正文。</p><script>不属于正文的脚本</script></div>')
        if path == "/broken-search":
            return self.respond('<a href="/broken-book">雨后借书处</a>')
        if path == "/broken-book":
            return self.respond('<h1>雨后借书处</h1><a href="/bad.txt">TXT 下载</a>')
        if path == "/bad.txt":
            return self.respond('<html><body>错误文件页面</body></html>')
        if path == "/file-search":
            return self.respond('<a href="/file-book">雨后借书处</a>')
        if path == "/file-book":
            return self.respond('<h1>雨后借书处</h1><a href="/valid.txt">TXT 下载</a>')
        if path == "/valid.txt":
            return self.respond(ORIGINAL, "text/plain; charset=utf-8")
        self.send_error(404)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SourceFixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.root = Path(__file__).resolve().parents[1] / ".functional-runs"
        cls.root.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.root)
        self.home = Path(self.temporary.name).resolve()
        assert self.home.is_relative_to(self.root.resolve())
        self.engine = Engine(self.home)
        SourceFixture.posted.clear()

    def tearDown(self):
        if self.engine.runner:
            self.engine.cancel_event.set()
            self.engine.runner.join(timeout=18)
        self.temporary.cleanup()

    def config(self, sources):
        self.engine.search.save_settings({"local_dirs": [], "wikisource": False, "web_sources": sources})

    def finish(self):
        self.engine.runner.join(timeout=15)
        self.assertFalse(self.engine.runner.is_alive())
        return self.engine.snapshot()["batches"][0]

    def test_auto_search_form_chapter_pages_and_zip(self):
        self.config([{"id": "original", "name": "原创测试源", "url": self.base + "/", "content_selector": "#content", "chapter_selector": ".toc a"}])
        self.engine.create(parse_titles("雨后借书处 | 原创作者"))
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        task = batch["tasks"][0]
        self.assertEqual((task["expected_chapters"], task["chapter_count"], task["page_count"]), (2, 2, 3))
        with zipfile.ZipFile(self.engine.archive_path(batch["id"])) as archive:
            text = archive.read("书籍/" + task["filename"]).decode()
            self.assertLess(text.index("第一章上半"), text.index("第一章下半"))
            self.assertLess(text.index("第一章下半"), text.index("第二章正文"))
            self.assertNotIn("不属于正文的脚本", text)
            report = json.loads(archive.read("下载报告.json"))
            self.assertEqual(report["books"][0]["chapter_count"], 2)

    def test_failed_file_switches_source_and_preserves_reason(self):
        self.config([
            {"id": "bad", "name": "失效文件源", "url": self.base, "search": {"url": "/broken-search"}},
            {"id": "good", "name": "可用原创源", "url": self.base, "search": {"url": "/file-search"}}])
        self.engine.create(parse_titles("雨后借书处"))
        batch = self.finish()
        self.assertEqual(batch["status"], "complete")
        task = batch["tasks"][0]
        self.assertEqual(task["source"], "可用原创源")
        self.assertEqual(task["source_failures"][0]["source"], "失效文件源")
        self.assertIn("网页", task["source_failures"][0]["error"])

    def test_legado_post_rules_import_encoding_and_restart(self):
        original = {"bookSourceName": "原创阅读规则", "bookSourceUrl": self.base,
                    "searchUrl": '/post,{"charset":"gb18030","method":"POST","body":"keyboard={{key}}&show=title"}',
                    "ruleToc": {"chapterList": ".toc@tag.a"}, "ruleContent": {"content": "id.content@all"}}
        self.config([original])
        restored = Engine(self.home)
        sources = restored.search.settings()["web_sources"]
        self.assertEqual(sources[0]["content_selector"], "#content")
        self.engine.create(parse_titles("雨后借书处"))
        self.assertEqual(self.finish()["status"], "complete")
        self.assertEqual(SourceFixture.posted[0]["keyboard"], "雨后借书处")
        with self.assertRaisesRegex(SourceError, "脚本"):
            validate_sources([{**original, "searchUrl": "@js:不可直接执行的规则"}])


if __name__ == "__main__":
    unittest.main()
