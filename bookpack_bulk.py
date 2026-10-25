"""Read source-provided whole-book text without third-party packages."""
from __future__ import annotations

import io
import re
import zipfile
from collections import Counter

from bookpack_content import chapter_problem


def decode_text(raw, declared=""):
    encodings = ["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else []
    encodings.extend(["utf-8-sig", declared, "gb18030", "big5"])
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return raw.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    raise ValueError("整本文件的文字编码无法识别。")


def text_files(raw, declared=""):
    if raw.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [row for row in archive.infolist() if not row.is_dir() and row.filename.lower().endswith(".txt")]
            if not members:
                raise ValueError("整本压缩包中没有 TXT 文件。")
            return [(row.filename, decode_text(archive.read(row))) for row in members]
    text = decode_text(raw, declared)
    if re.search(r"<(?:!doctype\s+html|html|head|body)(?:\s|>)", text[:2000], re.I):
        raise ValueError("整本下载入口返回了网页，已回退章节获取。")
    return [("", text)]


def chapter_key(title, normalize):
    return normalize(re.sub(r"^\s*\d+[.．、]\s*", "", title))


def merge_cached_extras(chapters, extras, normalize):
    """Retain source-file chapters across batches when the website still omits their numbered TOC entries."""
    def ordinal(chapter):
        match = re.match(r"^(\d+)[.．、]", chapter["title"])
        return int(match[1]) if match else None
    known = {chapter_key(row["title"], normalize) for row in chapters}
    existing_numbers = {ordinal(row) for row in chapters}
    additions = {}
    for row in extras:
        number = ordinal(row)
        if number is not None and number not in existing_numbers and chapter_key(row["title"], normalize) not in known:
            additions.setdefault(number, row)
    expanded = []
    for index, row in enumerate(chapters):
        expanded.append(row)
        left = ordinal(row)
        right = ordinal(chapters[index + 1]) if index + 1 < len(chapters) else None
        if left is not None and right is not None:
            expanded.extend(additions[number] for number in sorted(additions) if left < number < right)
    return expanded


def parse_sections(text, chapters, normalize, numbered=False):
    """Match full titles only. Numbered source TXT headers also delimit unmatched sections."""
    text = re.sub(r"<!--.*?-->", "", text.replace("\r\n", "\n").replace("\r", "\n"), flags=re.S)
    expected = {chapter_key(row["title"], normalize) for row in chapters}
    headers = []
    if not numbered and re.search(r"(?:^|\n)-{6,}[^\n]*\n", text):
        for match in re.finditer(r"(?:^|\n)-{6,}[^\n]*\n\s*([^\n]+)\n", text):
            title = match[1].strip()
            headers.append((match.start(), match.end(), title, None))
    else:
        offset = 0
        for line in text.splitlines(keepends=True):
            title = line.strip()
            # Source TXT bodies are indented; repeated inner headings are body, not boundaries.
            if title and len(title) <= 160 and not line[0].isspace():
                number = re.match(r"^(\d+)[.．、]\s*(\S.*)$", title)
                if numbered and number:
                    headers.append((offset, offset + len(line), title, int(number[1])))
                elif chapter_key(title, normalize) in expected or re.match(r"^第.{1,20}[章節节回卷]", title):
                    headers.append((offset, offset + len(line), title, None))
            offset += len(line)
    sections = []
    for index, (_, body_start, title, ordinal) in enumerate(headers):
        end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        body = text[body_start:end].strip()
        if index + 1 == len(headers):
            body = re.split(r"\n\s*=已完[结結]=\s*\n", body, maxsplit=1)[0].strip()
        first, separator, rest = body.partition("\n")
        if separator and chapter_key(first.strip(), normalize) == chapter_key(title, normalize):
            body = rest.strip()
        if not chapter_problem(body):
            sections.append({"title": title, "text": body, "key": chapter_key(title, normalize),
                             "full_key": normalize(title), "ordinal": ordinal})
    return sections


def match_sections(sections, chapters, normalize, bulk_url, numbered=False):
    """Keep unique title matches; preserve numbered extra chapters between adjacent TOC anchors."""
    section_counts = Counter(row["key"] for row in sections)
    full_counts = Counter(row["full_key"] for row in sections)
    toc_counts = Counter(chapter_key(row["title"], normalize) for row in chapters)
    toc_full_counts = Counter(normalize(row["title"]) for row in chapters)
    toc_indexes = {chapter_key(row["title"], normalize): index for index, row in enumerate(chapters)}
    toc_full_indexes = {normalize(row["title"]): index for index, row in enumerate(chapters)}
    matched, anchors = {}, {}
    for index, row in enumerate(sections):
        key = row["key"]
        if full_counts[row["full_key"]] == 1 and toc_full_counts[row["full_key"]] == 1:
            toc_index = toc_full_indexes[row["full_key"]]
        elif section_counts[key] == 1 and toc_counts[key] == 1:
            toc_index = toc_indexes[key]
        else:
            continue
        matched[chapters[toc_index]["url"]] = row
        anchors[index] = toc_index
    # An out-of-order file is not usable as a directory-aligned whole book.
    if list(anchors.values()) != sorted(anchors.values()):
        raise ValueError("整本文件的章节次序与当前目录不符。")
    expanded, added = list(chapters), 0
    if numbered:
        ordinals = {int(match[1]) for chapter in chapters
                    if (match := re.match(r"^(\d+)[.．、]", chapter["title"]))}
        inserts = {}
        pairs = list(anchors.items())
        for (left, toc_left), (right, toc_right) in zip(pairs, pairs[1:]):
            if toc_right != toc_left + 1:
                continue
            for row in sections[left + 1:right]:
                number = row["ordinal"]
                if (number is None or number in ordinals or row["key"] in toc_indexes
                        or section_counts[row["key"]] != 1
                        or not re.match(r"^\d+[.．、]\s*第.{1,20}[章节章節回]", row["title"])):
                    continue
                if not (sections[left]["ordinal"] is not None and sections[right]["ordinal"] is not None
                        and sections[left]["ordinal"] < number < sections[right]["ordinal"]):
                    continue
                chapter = {"title": row["title"], "url": bulk_url + "#chapter=" + str(number), "bulk_only": True}
                inserts.setdefault(toc_right, []).append(chapter)
                matched[chapter["url"]] = row
                ordinals.add(number)
                added += 1
        expanded = [row for index, chapter in enumerate(chapters) for row in [*inserts.get(index, []), chapter]]
    return matched, expanded, added
