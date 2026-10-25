"""Android entrypoint: real bookpack core with app-private state and a loopback UI."""
from pathlib import Path
import json
import logging
import sys
import threading
import urllib.parse

_lock = threading.RLock()
_server = None
_thread = None

def start(runtime, home):
    global _server, _thread
    with _lock:
        if _server is not None:
            return f'http://127.0.0.1:{_server.server_port}/'
        runtime, home = Path(runtime), Path(home)
        home.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(runtime))
        logging.basicConfig(filename=home / '运行日志.log', encoding='utf-8', level=logging.INFO)
        import bookpack_search
        try:
            from opencc import OpenCC
            converter = OpenCC('t2s')
            original = bookpack_search.normalize
            bookpack_search.normalize = lambda value: original(converter.convert(str(value or '')))
        except ImportError:
            logging.exception('Bundled OpenCC is missing')
            raise
        from app import AppServer, Handler
        from bookpack_core import Engine
        from bookpack_reader import ReaderLibrary

        class MobileHandler(Handler):
            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                static = {'/': ('index.html','text/html; charset=utf-8'),
                          '/mobile.js': ('mobile.js','application/javascript; charset=utf-8'),
                          '/mobile.css': ('mobile.css','text/css; charset=utf-8'),
                          '/radar-data.js': ('radar-data.js','application/javascript; charset=utf-8'),
                          '/assets/ocean.png': ('assets/ocean.png','image/png')}
                for file in ('scenes.js', 'reader.js', 'legado.js'):
                    static['/' + file] = (file, 'application/javascript; charset=utf-8')
                if path in static:
                    file, mime = static[path]
                    return self.send_bytes((runtime / 'mobile' / file).read_bytes(), mime)
                if path == '/api/mobile/info':
                    return self.send_json({'version':'0.4.0','core_version':'1.8.2','execution':'android-local'})
                if path.startswith('/api/reader/'):
                    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                    try:
                        reader = self.server.reader
                        if path == '/api/reader/library':
                            return self.send_json(reader.list())
                        if path == '/api/reader/book':
                            return self.send_json(reader.book(query.get('id', '')))
                        if path == '/api/reader/chapter':
                            return self.send_json(reader.chapter(query.get('id', ''), query.get('chapter', 0)))
                        if path == '/api/reader/search':
                            return self.send_json(reader.search(query.get('id', ''), query.get('q', '')))
                    except (ValueError, OSError) as exc:
                        return self.send_json({'error': str(exc)}, 400)
                return super().do_GET()

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == '/api/reader/import':
                    try:
                        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                        result = self.server.reader.import_file(query.get('filename', ''), self.rfile,
                                                               int(self.headers.get('Content-Length', 0)))
                        import_directory(str(self.server.engine.home / '本地书库'))
                        return self.send_json(result)
                    except (ValueError, OSError) as exc:
                        return self.send_json({'error': str(exc)}, 400)
                if path in ('/api/reader/progress', '/api/radar/daily', '/api/radar/auto'):
                    try:
                        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
                        if path == '/api/reader/progress':
                            return self.send_json(self.server.reader.save(body))
                        if path == '/api/radar/auto':
                            return self.send_json(self.server.radar.configure_daily(body.get('enabled', True)))
                        return self.send_json(self.server.radar.start_daily(bool(body.get('force'))))
                    except (ValueError, OSError) as exc:
                        return self.send_json({'error': str(exc)}, 400)
                if path in {'/api/open-folder','/api/shutdown'}:
                    return self.send_json({'error':'请使用手机的保存或分享操作。'}, 400)
                return super().do_POST()

        engine = Engine(home)
        server = AppServer(('127.0.0.1', 0), engine)
        server.reader = ReaderLibrary(engine)
        server.RequestHandlerClass = MobileHandler
        _server = server
        _thread = threading.Thread(target=server.serve_forever, name='BookpackHTTP', daemon=True)
        _thread.start()
        return f'http://127.0.0.1:{server.server_port}/'

def task_status():
    if _server is None:
        return json.dumps({'active':False,'title':''})
    batches = _server.engine.snapshot()['batches']
    active = next((batch for batch in batches if batch['status'] in ('running','stopping','packing')), None)
    radar = _server.radar.status()
    return json.dumps({'active': bool(active) or radar['running'], 'title': active['name'] if active else radar.get('message','') if radar['running'] else ''}, ensure_ascii=False)

def interrupt():
    if _server is not None:
        _server.engine.cancel_event.set()
        _server.engine._save()

def import_directory(directory):
    config = _server.engine.search.settings()
    if str(directory) not in config['local_dirs']:
        config['local_dirs'].append(str(directory))
        _server.engine.search.save_settings(config)
    return True

def reader_source(identity):
    return json.dumps(_server.reader.source(str(identity)), ensure_ascii=False)

def daily_update():
    status = _server.radar.start_daily()
    worker = _server.radar.worker
    if status['running'] and worker:
        worker.join(360)
        if worker.is_alive():
            stop_daily()
            return False
    return not _server.radar.status()['error']

def configure_daily(enabled):
    return _server.radar.configure_daily(bool(enabled))

def stop_daily():
    if _server and _server.radar.status().get('automatic'):
        _server.radar.stop.set()
