"""Start a persistent local browser preview with isolated original sample books."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time

project = Path(__file__).resolve().parents[1]
root = project.parent
sys.path.insert(0, str(root))
from app import DEMO_TEXT, demo_epub

home = root / '.ui-run' / 'legado-web-preview'
books = home / '本地书库'
books.mkdir(parents=True, exist_ok=True)
for name, body in [('雨后的借书处.txt', DEMO_TEXT.encode('utf-8')), ('雨后的借书处.epub', demo_epub())]:
    if not (books / name).exists():
        (books / name).write_bytes(body)
outputs = project / 'outputs'
outputs.mkdir(exist_ok=True)
log = outputs / 'web-preview.log'
with log.open('w', encoding='utf-8') as output:
    process = subprocess.Popen([sys.executable, '-X', 'utf8', str(project / 'tools/serve_local.py'), '--home', str(home)],
                               cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
for _ in range(120):
    content = log.read_text(encoding='utf-8')
    found = re.search(r'http://127\.0\.0\.1:\d+/', content)
    if found:
        result = {'pid': process.pid, 'origin': found[0], 'url': found[0] + 'preview', 'home': str(home)}
        (outputs / 'web-preview.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        break
    if process.poll() is not None:
        raise SystemExit(content)
    time.sleep(.1)
else:
    raise SystemExit('Preview did not become ready; inspect android/outputs/web-preview.log.')
