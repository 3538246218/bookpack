"""Stage bundled code/assets only; user tasks and downloaded files are never included."""
from pathlib import Path
import hashlib
import json
import shutil
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
RUNTIME = PROJECT / 'app/src/main/assets/runtime'
RUNTIME.mkdir(parents=True, exist_ok=True)
for source in [ROOT / 'app.py', *ROOT.glob('bookpack_*.py')]:
    shutil.copy2(source, RUNTIME / source.name)
for directory in ('vendor', 'vendor/fanqie_radar'):
    target = RUNTIME / directory
    target.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / directory).iterdir():
        if source.is_file(): shutil.copy2(source, target / source.name)
for source in (ROOT / 'data').rglob('*.json'):
    target = RUNTIME / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

third_party = PROJECT / 'third-party'
third_party.mkdir(exist_ok=True)
lock = third_party / 'opencc.lock.json'
if lock.is_file():
    dependency = json.loads(lock.read_text(encoding='utf-8'))
else:
    with urllib.request.urlopen('https://pypi.org/pypi/opencc-python-reimplemented/json', timeout=30) as response:
        metadata = json.load(response)
    wheel = next(item for item in metadata['urls'] if item['filename'].endswith('none-any.whl'))
    dependency = {'version':metadata['info']['version'], 'url':wheel['url'], 'sha256':wheel['digests']['sha256']}
    lock.write_text(json.dumps(dependency, indent=2), encoding='utf-8')
cache = Path('E:/dev-tools/downloads') / dependency['url'].rsplit('/',1)[1]
if not cache.is_file() or hashlib.sha256(cache.read_bytes()).hexdigest() != dependency['sha256']:
    with urllib.request.urlopen(dependency['url'], timeout=45) as response, cache.open('wb') as output:
        shutil.copyfileobj(response, output)
assert hashlib.sha256(cache.read_bytes()).hexdigest() == dependency['sha256']
with zipfile.ZipFile(cache) as package:
    package.extractall(RUNTIME)

mobile = RUNTIME / 'mobile'
mobile.mkdir(exist_ok=True)
prototype = ROOT / 'mobile-plan/prototype'
html = (prototype / 'index.html').read_text(encoding='utf-8')
start = html.index('<aside class="preview-notes">')
end = html.index('</aside>', start) + len('</aside>')
html = html[:start] + html[end:]
start = html.index('<p class="preview-label">')
end = html.index('</p>', start) + len('</p>')
html = html[:start] + html[end:]
html = html.replace('书包 · 移动端交互原型', '书包')
html = html.replace('保存在此浏览器', '保存在此手机').replace('连接与错误状态预览', '运行与存储')
html = html.replace('<p class="snapshot-footnote">书名与在读数据来自已保存快照，非今日实时榜单。</p>', '<p class="snapshot-footnote">榜单注明快照日期，刷新后显示新保存的数据。</p>')
(mobile / 'index.html').write_text(html, encoding='utf-8')
css = (prototype / 'mobile.css').read_text(encoding='utf-8') + '\n' + (PROJECT / 'web/android.css').read_text(encoding='utf-8')
(mobile / 'mobile.css').write_text(css, encoding='utf-8')
shutil.copy2(PROJECT / 'web/mobile.js', mobile / 'mobile.js')
(mobile / 'assets').mkdir(exist_ok=True)
shutil.copy2(prototype / 'assets/ocean.png', mobile / 'assets/ocean.png')
text = (prototype / 'radar-data.js').read_text(encoding='utf-8')
data = json.loads(text.split(' = ',1)[1].strip().removesuffix(';'))
data.pop('demoText',None)
(mobile / 'radar-data.js').write_text('window.BOOKPACK_BUNDLED_DATA = ' + json.dumps(data,ensure_ascii=False,separators=(',',':')) + ';',encoding='utf-8')
digest = hashlib.sha256()
for file in sorted(RUNTIME.rglob('*')):
    if file.is_file() and file.name != 'build-id.txt':
        digest.update(file.relative_to(RUNTIME).as_posix().encode('utf-8'))
        digest.update(file.read_bytes())
(RUNTIME / 'build-id.txt').write_text(digest.hexdigest(), encoding='ascii')
print(json.dumps({'runtime_files':sum(1 for file in RUNTIME.rglob('*') if file.is_file()),'opencc':dependency['version'],'runtime_bytes':sum(file.stat().st_size for file in RUNTIME.rglob('*') if file.is_file())}))
