"""Exercise the Android HTTP adapter with isolated state on the development host."""
from pathlib import Path
import argparse
import sys
import time

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--home',type=Path,required=True)
args = parser.parse_args()
sys.path.insert(0,str(project / 'app/src/main/python'))
import android_runtime
origin = android_runtime.start(project / 'app/src/main/assets/runtime', args.home)
mobile_handler = android_runtime._server.RequestHandlerClass

class PreviewHandler(mobile_handler):
    def do_GET(self):
        if self.path.split('?', 1)[0] == '/preview':
            return self.send_bytes((project / 'tools/web_preview.html').read_bytes(), 'text/html; charset=utf-8')
        return super().do_GET()

android_runtime._server.RequestHandlerClass = PreviewHandler
print(origin,flush=True)
try:
    while True: time.sleep(.2)
except KeyboardInterrupt:
    pass
