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

        class MobileHandler(Handler):
            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                static = {'/': ('index.html','text/html; charset=utf-8'),
                          '/mobile.js': ('mobile.js','application/javascript; charset=utf-8'),
                          '/mobile.css': ('mobile.css','text/css; charset=utf-8'),
                          '/radar-data.js': ('radar-data.js','application/javascript; charset=utf-8'),
                          '/assets/ocean.png': ('assets/ocean.png','image/png')}
                if path in static:
                    file, mime = static[path]
                    return self.send_bytes((runtime / 'mobile' / file).read_bytes(), mime)
                if path == '/api/mobile/info':
                    return self.send_json({'version':'0.1.0','core_version':'1.8.2','execution':'android-local'})
                return super().do_GET()

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                if path in {'/api/open-folder','/api/shutdown'}:
                    return self.send_json({'error':'请使用手机的保存或分享操作。'}, 400)
                return super().do_POST()

        engine = Engine(home)
        server = AppServer(('127.0.0.1', 0), engine)
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
