"""Stage bundled code/assets only; user tasks and downloaded files are never included."""
from pathlib import Path
import hashlib
import json
import os
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
cache_dir = Path(os.environ.get('BOOKPACK_DEPENDENCY_CACHE',
                 'E:/dev-tools/downloads' if os.name == 'nt' and Path('E:/dev-tools/downloads').is_dir()
                 else str(PROJECT / 'outputs/dependency-cache')))
cache_dir.mkdir(parents=True, exist_ok=True)
cache = cache_dir / dependency['url'].rsplit('/',1)[1]
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
html = html.replace('<script src="mobile.js" defer></script>', '<script src="scenes.js" defer></script><script src="mobile.js" defer></script><script src="legado.js" defer></script><script src="reader.js" defer></script>')
html = html.replace('<section class="page detail-page"', (PROJECT / 'web/library.html').read_text(encoding='utf-8') + '<section class="page detail-page"')
html = html.replace('</body>', (PROJECT / 'web/reader.html').read_text(encoding='utf-8') + '</body>')
html = html.replace('</body>', (PROJECT / 'web/legado-dialog.html').read_text(encoding='utf-8') + '</body>')
html = html.replace('<button type="button" data-nav="bags">', '<button type="button" data-nav="library"><svg class="icon"><use href="#i-book"/></svg><span>书架</span></button><button type="button" data-nav="bags">')
scenes = '<div class="scene-options" aria-label="景色选择">' + ''.join(f'<button type="button" class="scene-option scene-{key}" data-scene="{key}"><span class="scene-miniature" aria-hidden="true"></span><strong>{name}</strong><small>{detail}</small></button>' for key, name, detail in [('ocean','海岸晨光','细浪与暖光'),('forest','林间微雨','护眼绿'),('stars','星夜远山','黑夜模式'),('rain','雨夜窗景','深蓝夜色')]) + '</div>'
html = html.replace('<div class="ocean-presets">', scenes + '<div class="ocean-presets">')
html = html.replace('动态海面', '动态景色').replace('海浪大小', '动态强度').replace('aria-label="海面外观"', 'aria-label="景色与外观"')
html = html.replace('海面、日光，和阅读前的片刻。', '景色、光线，和阅读时的安静。')
html = html.replace('</section>\n              <div class="section-label"><h3>书源与连接', '<p class="motion-status" id="motion-status" role="status"></p></section>\n              <div class="section-label"><h3>书源与连接')
html = html.replace('<details class="radar-discover', '<p class="radar-freshness" id="radar-freshness"></p><details class="radar-discover')
html = html.replace('<section class="settings-card glass settings-links">', '<section class="settings-card glass"><button class="setting-row toggle-row" id="toggle-radar-auto" type="button" aria-pressed="true"><span>每日更新番茄雷达</span><span class="switch" aria-hidden="true"></span></button><p id="radar-auto-status" class="settings-footnote"></p></section><section class="settings-card glass settings-links">')
html = html.replace('<section class="settings-card glass settings-links">', (PROJECT / 'web/reader-integration.html').read_text(encoding='utf-8') + '<section class="settings-card glass settings-links">')
(mobile / 'index.html').write_text(html, encoding='utf-8')
css = (prototype / 'mobile.css').read_text(encoding='utf-8') + '\n' + (PROJECT / 'web/android.css').read_text(encoding='utf-8')
(mobile / 'mobile.css').write_text(css, encoding='utf-8')
shutil.copy2(PROJECT / 'web/mobile.js', mobile / 'mobile.js')
for script in ('scenes.js', 'reader.js', 'legado.js'):
    shutil.copy2(PROJECT / 'web' / script, mobile / script)
(mobile / 'assets').mkdir(exist_ok=True)
shutil.copy2(prototype / 'assets/ocean.png', mobile / 'assets/ocean.png')
text = (prototype / 'radar-data.js').read_text(encoding='utf-8')
data = json.loads(text.split(' = ',1)[1].strip().removesuffix(';'))
data.pop('demoText',None)
import sys
sys.path.insert(0, str(ROOT))
from vendor.fanqie_radar.schema import load_books
for channel in ('male', 'female'):
    for rank in ('read', 'new'):
        snapshots = sorted((ROOT / 'data/radar').glob(f'fanqie_{channel}_{rank}_ranks_*.json'))
        if snapshots:
            data['boards'][channel + '/' + rank] = {'items':load_books(snapshots[-1])}
(mobile / 'radar-data.js').write_text('window.BOOKPACK_BUNDLED_DATA = ' + json.dumps(data,ensure_ascii=False,separators=(',',':')) + ';',encoding='utf-8')
digest = hashlib.sha256()
runtime_files = [file for file in sorted(RUNTIME.rglob('*')) if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc']
for file in runtime_files:
    if file.name != 'build-id.txt':
        digest.update(file.relative_to(RUNTIME).as_posix().encode('utf-8'))
        digest.update(file.read_bytes())
(RUNTIME / 'build-id.txt').write_text(digest.hexdigest(), encoding='ascii')
print(json.dumps({'runtime_files':len(runtime_files),'opencc':dependency['version'],'runtime_bytes':sum(file.stat().st_size for file in runtime_files)}))
