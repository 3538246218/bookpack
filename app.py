from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bookpack_core import BookpackError, Engine, parse_tasks
from bookpack_search import parse_titles, validate_books
from bookpack_radar import RadarService

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web"
VERSION = "1.8.2"
for stream in (sys.stdout, sys.stderr):
    if stream and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
DEMO_TEXT = (
    "书包功能演示：雨后的借书处\n\n"
    "第一章 雨声\n"
    "雨停的时候，小镇的借书处还亮着灯。值班员把最后一张借书卡夹进本子，"
    "听见门口有人轻轻敲了三下。来人手中没有书，只有一片被雨水洗净的银杏叶。\n\n"
    "第二章 回信\n"
    "叶片背后写着一行很小的字：谢谢你一直为晚归的人留灯。"
    "值班员笑了笑，把叶片放进空书签盒，决定明天早点开门。\n\n"
    "全文结束。以上为软件作者创作的功能测试短文，不是真实小说下载结果。\n"
)


def demo_epub():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr("content.opf", '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">bookpack-original-demo</dc:identifier><dc:title>雨后的借书处</dc:title><dc:language>zh-CN</dc:language></metadata><manifest><item id="text" href="text.xhtml" media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="text"/></spine></package>')
        archive.writestr("toc.ncx", '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="bookpack-original-demo"/></head><docTitle><text>雨后的借书处</text></docTitle><navMap><navPoint id="first" playOrder="1"><navLabel><text>正文</text></navLabel><content src="text.xhtml"/></navPoint></navMap></ncx>')
        paragraphs = "".join("<p>" + paragraph + "</p>" for paragraph in DEMO_TEXT.split("\n") if paragraph)
        archive.writestr("text.xhtml", '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>雨后的借书处</title></head><body>' + paragraphs + "</body></html>")
    return output.getvalue()


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine):
        super().__init__(address, Handler)
        self.engine = engine
        self.radar = RadarService(engine.home, engine.search.platforms)
        base = f"http://127.0.0.1:{self.server_address[1]}"
        self.demo_books = [
            {"title": "雨后的借书处（UTF-8 演示）", "url": base + "/demo/original.txt", "source": "内置原创演示"},
            {"title": "雨后的借书处（GBK 演示）", "url": base + "/demo/gbk.txt", "source": "内置原创演示"},
            {"title": "雨后的借书处（EPUB 演示）", "url": base + "/demo/original.epub", "source": "内置原创演示"}]
        engine.search.extra_books = validate_books(self.demo_books)


class Handler(BaseHTTPRequestHandler):
    server_version = "BookPack/1.8.2"

    def log_message(self, fmt, *args):
        logging.debug(fmt, *args)

    def send_bytes(self, data, content_type, status=200, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        engine = self.server.engine
        if path == "/api/radar":
            query = {key: values[0] for key, values in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            try:
                return self.send_json(self.server.radar.browse(query))
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if path == "/api/radar/status":
            return self.send_json(self.server.radar.status())
        if path == "/api/ping":
            return self.send_json({"app": "bookpack", "version": VERSION, "home": str(engine.home)})
        if path == "/api/state":
            full = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("full") == ["1"]
            return self.send_json(engine.snapshot(compact=not full))
        if path == "/api/sources":
            return self.send_json(engine.search.settings())
        if path == "/api/web-sources":
            return self.send_json({"sources": engine.search.sources.snapshot(engine.search.settings()["web_sources"]),
                "unresolved": [{"name": "雨鹿小说", "url": "novel.yulu.local", "reason": "已检索公开书源规则，暂未定位到此标识对应的真实接口；尚未接通。"},
                               {"name": "雨鹿阅读-漫画聚合", "url": "comic.yulu.local", "reason": "漫画书源，当前软件处理 TXT／EPUB。"}]})
        if path == "/api/demo":
            return self.send_json({"text": "\n".join(row["title"] for row in self.server.demo_books),
                                   "links": "\n".join(row["title"] + " | " + row["url"] for row in self.server.demo_books)})
        if path.startswith("/api/archive/"):
            try:
                archive = engine.archive_path(path.rsplit("/", 1)[1])
                size = archive.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(archive.name))
                self.end_headers()
                with archive.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(256 * 1024), b""):
                        self.wfile.write(chunk)
            except BookpackError as exc:
                self.send_json({"error": str(exc)}, 404)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except OSError:
                self.send_json({"error": "书包文件已被移动或删除，请点击继续未完成任务重新打包。"}, 404)
            return
        if path == "/demo/original.txt":
            return self.send_bytes(DEMO_TEXT.encode("utf-8"), "text/plain; charset=utf-8")
        if path == "/demo/gbk.txt":
            return self.send_bytes(DEMO_TEXT.encode("gb18030"), "text/plain; charset=gb18030")
        if path == "/demo/original.epub":
            return self.send_bytes(demo_epub(), "application/epub+zip")
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.css": ("app.css", "text/css; charset=utf-8"),
                 "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                 "/search-suggestions.js": ("search-suggestions.js", "application/javascript; charset=utf-8"),
                 "/ocean-controls.js": ("ocean-controls.js", "application/javascript; charset=utf-8")}
        files["/radar.js"] = ("radar.js", "application/javascript; charset=utf-8")
        files["/radar.css"] = ("radar.css", "text/css; charset=utf-8")
        files["/feedback.js"] = ("feedback.js", "application/javascript; charset=utf-8")
        files["/feedback.css"] = ("feedback.css", "text/css; charset=utf-8")
        ocean_files = ["ocean.js", "vendor/three.core.js", "vendor/three.tsl.js", "vendor/three.webgpu.js",
                       "vendor/addons/controls/OrbitControls.js", "vendor/addons/tsl/display/BloomNode.js"]
        files.update({"/ocean/" + name: ("ocean/" + name, "application/javascript; charset=utf-8") for name in ocean_files})
        files["/ocean/frame.html"] = ("ocean/frame.html", "text/html; charset=utf-8")
        if (STATIC / "ocean/poster.png").is_file():
            files["/ocean/poster.png"] = ("ocean/poster.png", "image/png")
        if path in files:
            filename, content_type = files[path]
            return self.send_bytes((STATIC / filename).read_bytes(), content_type)
        if path == "/favicon.ico":
            return self.send_bytes(b"", "image/x-icon", 204)
        self.send_json({"error": "找不到这个地址。"}, 404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        engine = self.server.engine
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
            if path == "/api/radar/refresh":
                return self.send_json(self.server.radar.start_refresh(body.get("channel", "male"), body.get("rank", "read")), 202)
            if path == "/api/radar/select":
                return self.send_json(self.server.radar.select(body.get("book_id", ""), body.get("channel", "male"), body.get("rank", "read")))
            if path == "/api/sources":
                return self.send_json(engine.search.save_settings(body))
            if path == "/api/web-sources":
                config = engine.search.settings()
                config["web_sources"] = body.get("sources", [])
                engine.search.save_settings(config)
                return self.send_json({"sources": engine.search.sources.snapshot(engine.search.settings()["web_sources"])})
            if path == "/api/preview":
                parser = parse_tasks if body.get("mode") == "links" else parse_titles
                return self.send_json({"tasks": parser(body.get("text", ""))})
            if path == "/api/suggest":
                return self.send_json(engine.suggest(body.get("query", ""), bool(body.get("online", False)),
                                                     body.get("client_id", ""), body.get("generation", 0)))
            if path == "/api/start":
                parser = parse_tasks if body.get("mode") == "links" else parse_titles
                tasks = parser(body.get("text", ""))
                batch_id = engine.create(tasks, body.get("name", ""), body.get("concurrency", 3))
                return self.send_json({"id": batch_id}, 201)
            if path == "/api/cancel":
                engine.cancel(body.get("id", ""))
                return self.send_json({"ok": True})
            if path == "/api/retry":
                engine.retry(body.get("id", ""))
                return self.send_json({"ok": True})
            if path == "/api/choose":
                engine.choose(body.get("id", ""), body.get("task_id", ""), body.get("candidate_id", ""))
                return self.send_json({"ok": True})
            if path == "/api/open-folder":
                folder = engine.batch_directory(body["id"]) if body.get("id") else engine.output
                if not folder.exists():
                    raise BookpackError("输出文件夹还没有创建。")
                if os.name == "nt":
                    os.startfile(str(folder))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
                return self.send_json({"ok": True})
            if path == "/api/shutdown":
                self.server.radar.close()
                engine.cancel_event.set()
                self.send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_json({"error": "找不到这个操作。"}, 404)
        except (BookpackError, ValueError, KeyError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            logging.exception("操作失败")
            self.send_json({"error": f"操作失败：{exc}"}, 500)


def open_browser(url):
    if os.name == "nt":
        for root, suffix in [(os.environ.get("PROGRAMFILES", ""), "Google/Chrome/Application/chrome.exe"),
                             (os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft/Edge/Application/msedge.exe")]:
            browser = Path(root) / suffix
            if browser.is_file():
                subprocess.Popen([str(browser), "--new-window", url], creationflags=subprocess.CREATE_NO_WINDOW)
                return
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="书包：本地批量下载与打包")
    parser.add_argument("--home", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=43721)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    args.home.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.home / "运行日志.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    runtime_file = args.home / "运行地址.json"
    if args.open and runtime_file.exists():
        try:
            saved = json.loads(runtime_file.read_text(encoding="utf-8"))
            with urllib.request.urlopen(saved["url"] + "api/ping", timeout=2) as response:
                ping = json.load(response)
            if ping.get("app") == "bookpack" and Path(ping.get("home", "")).resolve() == args.home.resolve():
                open_browser(saved["url"])
                return
        except Exception:
            pass
    engine = Engine(args.home)
    server = None
    for port in ([0] if args.port == 0 else range(args.port, args.port + 10)):
        try:
            server = AppServer(("127.0.0.1", port), engine)
            break
        except OSError:
            continue
    if server is None:
        raise BookpackError("本地端口被占用，请用 --port 指定其他端口。")
    url = f"http://127.0.0.1:{server.server_port}/"
    runtime_file.write_text(json.dumps({"url": url, "pid": os.getpid(), "version": VERSION}, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("书包已启动 %s", url)
    if sys.stdout:
        print(url, flush=True)
    if args.open:
        open_browser(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        engine.cancel_event.set()
    finally:
        server.radar.close()
        if engine.runner and engine.runner.is_alive():
            engine.cancel_event.set()
            engine.runner.join(timeout=18)
        server.server_close()
        engine.suggestions.close()
        engine._save()
        if runtime_file.exists():
            try:
                if json.loads(runtime_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    runtime_file.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.exception("启动失败")
        if sys.stderr:
            print(str(error), file=sys.stderr)
        else:
            import tkinter.messagebox
            tkinter.messagebox.showerror("书包启动失败", str(error))
        sys.exit(1)
