"""Local TXT/EPUB bookshelf with chapter reading and persistent progress."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import threading
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

LIMIT = 64 * 1024 * 1024
HEADING = re.compile(r"^\s*(?:第[零〇一二三四五六七八九十百千万两\d]{1,20}[章回节卷部篇].{0,65}|(?:chapter|volume)\s+\S.{0,60}|序章|序言|楔子|尾声|后记|番外.{0,50})\s*$", re.I)


class ReaderError(ValueError):
    pass


def decode_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ReaderError('文字编码无法识别，请使用 UTF-8 或 GB18030 的 TXT。')


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.skip = [], [], 0
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.skip += 1
        if self.skip:
            return
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'li', 'section'):
            self.parts.append('\n')
        if tag in ('h1', 'h2') and not self.title:
            self.in_heading = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.skip = max(0, self.skip - 1)
        if tag in ('h1', 'h2'):
            self.in_heading = False
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'li', 'section'):
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)
            if self.in_heading:
                self.title.append(text)


def sections(title, text):
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return []
    # Limit the DOM to one small section, splitting at actual paragraph boundaries.
    rows, buffer, length, part = [], [], 0, 1
    for paragraph in text.split('\n'):
        for start in range(0, max(1, len(paragraph)), 6000):
            item = paragraph[start:start + 6000]
            if length + len(item) > 7000 and buffer:
                rows.append({'title': title if part == 1 else f'{title}（续 {part}）', 'text': '\n'.join(buffer)})
                buffer, length, part = [], 0, part + 1
            buffer.append(item)
            length += len(item) + 1
    if buffer:
        rows.append({'title': title if part == 1 else f'{title}（续 {part}）', 'text': '\n'.join(buffer)})
    return rows


def parse_txt(path):
    if path.stat().st_size > LIMIT:
        raise ReaderError('这本 TXT 超过 64 MB，请先拆分为分卷文件。')
    text = decode_text(path.read_bytes())
    result, heading, buffer = [], '正文', []
    for line in text.splitlines():
        if HEADING.fullmatch(line) and buffer:
            result.extend(sections(heading, '\n'.join(buffer)))
            heading, buffer = line.strip(), []
        elif HEADING.fullmatch(line):
            heading = line.strip()
        else:
            buffer.append(line)
    result.extend(sections(heading, '\n'.join(buffer)))
    return result


def parse_epub(path):
    try:
        with zipfile.ZipFile(path) as package:
            if sum(row.file_size for row in package.infolist()) > LIMIT:
                raise ReaderError('这本 EPUB 解压后超过 64 MB，请使用分卷版本。')
            container = ET.fromstring(package.read('META-INF/container.xml'))
            entry = container.find('.//{*}rootfile')
            opf_path = entry.attrib['full-path']
            opf = ET.fromstring(package.read(opf_path))
            manifest = {row.attrib['id']: row.attrib for row in opf.findall('.//{*}manifest/{*}item')}
            result = []
            for index, spine in enumerate(opf.findall('.//{*}spine/{*}itemref')):
                if spine.attrib.get('linear') == 'no':
                    continue
                item = manifest.get(spine.attrib.get('idref'), {})
                href = urllib.parse.unquote(urllib.parse.urlsplit(item.get('href', '')).path)
                if not href:
                    continue
                member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
                parser = PlainHTML()
                parser.feed(decode_text(package.read(member)))
                title = ''.join(parser.title).strip() or f'第 {index + 1} 节'
                result.extend(sections(title, ''.join(parser.parts)))
            return result
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ReaderError('这本 EPUB 的目录或正文无法读取，请重新导入完整文件。') from exc


class ReaderLibrary:
    def __init__(self, engine):
        self.engine = engine
        self.file = engine.home / '阅读记录.json'
        self.lock = threading.RLock()
        self.cache = None
        try:
            self.records = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else {}
        except (OSError, ValueError):
            self.records = {}

    def _books(self):
        found = {}
        with self.engine.lock:
            for batch in self.engine.batches:
                for task in batch['tasks']:
                    if task.get('status') != 'done' or not task.get('filename'):
                        continue
                    path = self.engine.output / batch['directory'] / '书籍' / task['filename']
                    if path.is_file():
                        found[str(path.resolve())] = (path, task.get('matched_title') or task['title'], task.get('matched_author') or task.get('author', ''), batch['id'], task['id'])
        for path in (self.engine.home / '本地书库').glob('*'):
            if path.is_file() and path.suffix.lower() in ('.txt', '.epub'):
                found.setdefault(str(path.resolve()), (path, path.stem, '', '', ''))
        return {hashlib.sha256(key.encode()).hexdigest()[:20]: value for key, value in found.items()}

    def list(self):
        rows = []
        for identity, (path, title, author, batch_id, task_id) in self._books().items():
            progress = dict(self.records.get(identity, {}))
            rows.append({'id': identity, 'title': title, 'author': author, 'format': path.suffix[1:].upper(),
                         'bytes': path.stat().st_size, 'progress': progress, 'batch_id': batch_id, 'task_id': task_id})
        rows.sort(key=lambda row: (row['progress'].get('updated_at', ''), row['title']), reverse=True)
        return {'books': rows}

    def source(self, identity):
        book = self._books().get(identity)
        if not book:
            raise ReaderError('文件已不存在，请重新导入或获取这本书。')
        path, title, author, _, _ = book
        if path.suffix.lower() not in ('.txt', '.epub'):
            raise ReaderError('请选择 TXT 或 EPUB 书籍。')
        return {'id': identity, 'path': str(path.resolve()), 'filename': path.name,
                'title': title, 'author': author, 'bytes': path.stat().st_size,
                'mime': 'application/epub+zip' if path.suffix.lower() == '.epub' else 'text/plain'}

    def import_file(self, filename, stream, length):
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(filename)).strip(' .')
        name = Path(filename)
        if name.suffix.lower() not in ('.txt', '.epub'):
            raise ReaderError('请选择 TXT 或 EPUB 文件。')
        if not 0 < length <= LIMIT:
            raise ReaderError('请选择 64 MB 以内的 TXT 或 EPUB 文件。')
        directory = self.engine.home / '本地书库'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / (uuid.uuid4().hex + '.part')
        try:
            with temporary.open('xb') as output:
                remaining = length
                while remaining:
                    chunk = stream.read(min(128 * 1024, remaining))
                    if not chunk:
                        raise ReaderError('文件没有上传完整，请重新选择。')
                    output.write(chunk)
                    remaining -= len(chunk)
            with self.lock:
                target, number = directory / name.name, 2
                while target.exists():
                    target = directory / f'{name.stem}（{number}）{name.suffix}'
                    number += 1
                temporary.rename(target)
            identity = hashlib.sha256(str(target.resolve()).encode()).hexdigest()[:20]
            return {'id': identity, 'title': target.stem, 'format': target.suffix[1:].upper()}
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self, identity):
        book = self._books().get(identity)
        if not book:
            raise ReaderError('文件已不存在，请重新导入或获取这本书。')
        path, title, author, _, _ = book
        signature = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        with self.lock:
            if self.cache is None or self.cache[0] != signature:
                chapters = parse_epub(path) if path.suffix.lower() == '.epub' else parse_txt(path)
                if not chapters:
                    raise ReaderError('文件里没有可阅读的文字；暂不支持纯图片 EPUB。')
                self.cache = signature, chapters
            return title, author, self.cache[1]

    def book(self, identity):
        title, author, chapters = self._load(identity)
        return {'id': identity, 'title': title, 'author': author,
                'chapters': [{'title': row['title'], 'characters': len(row['text'])} for row in chapters],
                'progress': dict(self.records.get(identity, {}))}

    def chapter(self, identity, index):
        _, _, chapters = self._load(identity)
        index = max(0, min(len(chapters) - 1, int(index)))
        return {'index': index, 'total': len(chapters), **chapters[index]}

    def search(self, identity, query):
        _, _, chapters = self._load(identity)
        query = str(query).strip()[:80]
        if not query:
            return {'matches': []}
        rows = []
        for index, chapter in enumerate(chapters):
            position = chapter['text'].find(query)
            if position >= 0:
                rows.append({'chapter': index, 'title': chapter['title'], 'snippet': chapter['text'][max(0, position - 24):position + len(query) + 55],
                             'offset': position / max(1, len(chapter['text']))})
            if len(rows) == 50:
                break
        return {'matches': rows}

    def save(self, body):
        identity = body.get('id', '')
        _, _, chapters = self._load(identity)
        index = max(0, min(len(chapters) - 1, int(body.get('chapter', 0))))
        offset = max(0., min(1., float(body.get('offset', 0))))
        with self.lock:
            row = self.records.setdefault(identity, {})
            row.update(chapter=index, offset=offset, updated_at=datetime.now().astimezone().isoformat(),
                       percent=round((index + offset) / len(chapters) * 100, 1))
            bookmarks = row.setdefault('bookmarks', [])
            if body.get('action') == 'bookmark':
                bookmarks.append({'id': uuid.uuid4().hex[:12], 'chapter': index, 'offset': offset,
                                  'label': str(body.get('label') or chapters[index]['title'])[:100]})
                del bookmarks[:-100]
            elif body.get('action') == 'remove_bookmark':
                row['bookmarks'] = [mark for mark in bookmarks if mark['id'] != body.get('bookmark_id')]
            temporary = self.file.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.records, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.file)
            return dict(row)
