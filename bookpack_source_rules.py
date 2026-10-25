"""HTTP source rules checked against real search, book and chapter pages."""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse


EXTRA_SOURCES = [
    {"id": "bqg_info", "name": "笔趣阁info", "url": "https://www.bqg.info/", "enabled": True,
     "search": {"url": "/search/", "params": {"searchkey": "{title}"}},
     "result_selector": ".book-coverlist h4 a", "chapter_selector": "#newlist a", "include_all_toc_entries": True,
     "content_selector": "#booktxt", "content_remove_patterns": [r"(?m)^\[笔趣阁信息\]:bqg\.info[^\n]*$", r"(?m)^\s*𝙱\s*q\s*ⓖ\.𝙸n\s*f\s*O\s*$"]},
    {"id": "xiangshu", "name": "香书小说", "url": "http://www.ibiqugu.net/", "enabled": True,
     "search": {"url": "/modules/article/waps.php", "method": "POST", "params": {"searchkey": "{title}"}},
     "result_selector": "td.even a", "chapter_selector": "#list dd a", "include_all_toc_entries": True,
     "content_selector": "#content", "content_remove_patterns": [r"(?m)^\s*最新网址：[^\n]*$", r"(?m)^手机站全新改版升级地址[^\n]*$", r"(?m)^.*看本书的最后都找到了漂亮的老婆哦[^\n]*$", r"(?m)^\s*亲,点击进去,给个好评呗,[^\n]*香书小说[^\n]*$"]},
    {"id": "quanben", "name": "全本小说网", "url": "https://quanben5.com/", "enabled": True, "kind": "quanben",
     "result_selector": "h3 a", "author_selector": ".pic_txt_list .author",
     "toc_link_pattern": r"/n/[^/]+/xiaoshuo\.html$", "chapter_selector": ".box .list a", "include_all_toc_entries": True,
     "content_selector": "#content"},
    {"id": "bijian", "name": "笔尖中文", "url": "http://www.xbiquwk.com/", "enabled": True,
     "search": {"url": "/modules/article/search.php", "method": "POST", "params": {"searchkey": "{title}"}},
     "result_selector": "td a", "chapter_selector": "#list dd a", "include_all_toc_entries": True,
     "content_selector": "#content"},
    {"id": "yueduku", "name": "阅读库", "url": "http://www.yeudusk.com/", "enabled": True,
     "search": {"url": "/modules/article/search.php", "params": {"q": "{title}"}},
     "toc_link_pattern": r"/html/\d+/\d+/$", "chapter_selector": ".chapters li.chapter a", "include_all_toc_entries": True,
     "content_selector": "#clickeye_content", "content_remove_patterns": [r"\(阅读库\s*www\.yeudusk\.com\)", r"(?:阅读库|yeudusk)\s*www\.yeudusk\.com"]},
    {"id": "niaoshu", "name": "鸟书网", "url": "http://www.99wx.info/", "enabled": True,
     "search": {"url": "/read/search/", "method": "POST", "params": {"searchkey": "{title}"}},
     "result_selector": ".bookinfo h4 a", "chapter_selector": "dl dd a", "include_all_toc_entries": True,
     "content_selector": "#content", "content_remove_patterns": [r"(?m)^\s*请记住本书首发域名：99wx\.info。鸟书网手机版阅读网址：[^\n]*$"]},
    {"id": "youjiu", "name": "悠久小说网", "url": "http://www.ujxsw.org/", "enabled": True,
     "search": {"url": "/searchbooks.php", "method": "POST", "params": {"searchkey": "{title}"}},
     "result_selector": ".shulist li.three a", "book_title_selector": ".d_title h1", "book_title_pattern": r"^(.*?)作者[：:]",
     "author_selector": ".d_title h1 a", "toc_link_pattern": r"/read/\d+/$",
     "chapter_selector": "#readerlist a", "include_all_toc_entries": True, "content_selector": ".read-content p",
     "content_remove_patterns": [r"(?m)^\s*【悠久小説網[^\n]*$", r"(?m)^\s*佰度搜索\s*【悠久小說網[^\n]*$"]},
    {"id": "biquge365", "name": "笔趣阁365", "url": "https://www.biquge365.net/", "enabled": True,
     "retry_keywords": False,
     "search": {"url": "/s.php", "method": "POST", "params": {"type": "articlename", "s": "{title}"}},
     "result_selector": ".menu span.name a", "toc_link_pattern": r"/newbook/\d+/$",
     "chapter_selector": ".border ul li a", "include_all_toc_entries": True, "content_selector": "#txt",
     "content_remove_patterns": [r"(?m)^.*一秒记住【笔趣阁】biquge365\.net[^\n]*$"]},
    {"id": "linovel", "name": "轻之文库", "url": "https://www.linovel.net/", "enabled": True,
     "search": {"url": "/search/", "params": {"kw": "{title}"}},
     "result_selector": "a.search-book", "result_title_pattern": r"^([^\n]+)",
     "book_title_selector": "h1.book-title", "author_selector": ".novelist .name a",
     "chapter_selector": ".chapter-list .chapter a", "include_all_toc_entries": True,
     "chapter_group_selector": ".section", "chapter_group_title_selector": ".volume-title",
     "content_selector": ".article-text"},
    {"id": "d8cms", "name": "新笔趣阁（分目录）", "url": "https://www.xbiquge.info/", "enabled": True,
     "mirrors": ["https://www.d8zw.com/"], "search": {"url": "/search.php", "params": {"q": "{title}"}},
     "result_selector": ".box.hot h3 a", "result_title_strip": r"^\[[^\]]*\]\s*",
     "toc_link_pattern": r"/\d+/\d+/index_1\.html$", "toc_page_selector": ".pagination a",
     "toc_page_pattern": r"/\d+/\d+/index_(\d+)\.html$", "chapter_selector": ".book_list2 a",
     "latest_selector": ".book_list a", "latest_exclude_selector": ".book_list2 a", "latest_descending": True,
     "include_all_toc_entries": True, "content_selector": "article.font_max", "next_page_selector": "#next1",
     "chapter_workers": 3, "content_remove_patterns": [r"(?m)^\s*第\(\d+/\d+\)页\s*$"]},
]


def result_title(node, source, clean):
    value = clean(node.text())
    if source.get("result_title_pattern"):
        match = re.search(source["result_title_pattern"], value)
        value = match[1].strip() if match else ""
    if source.get("result_title_strip"):
        value = re.sub(source["result_title_strip"], "", value).strip()
    return value


def quanben_request(base, title):
    """Encode the public search form's query parameter; no page JavaScript is executed."""
    alphabet = "PXhw7UT1B0a9kQDKZsjIASmOezxYG4CHo5Jyfg2b8FLpEvRr3WtVnlqMidu6cN"
    encoded = []
    for character in urllib.parse.quote(title, safe=";/?:@&=+$,#"):
        middle = alphabet[(alphabet.index(character) + 3) % 62] if character in alphabet else character
        encoded.append(random.choice(alphabet) + middle + random.choice(alphabet))
    parameters = {"c": "book", "a": "search.json", "callback": "search", "keywords": title,
                  "t": str(int(time.time() * 1000)), "b": "".join(encoded)}
    return urllib.parse.urljoin(base, "/") + "?" + urllib.parse.urlencode(parameters)


def quanben_result(text):
    match = re.match(r"\s*search\s*\(\s*", text)
    if not match:
        raise ValueError("全本小说网未返回搜索数据。")
    data, _ = json.JSONDecoder().raw_decode(text, match.end())
    if not isinstance(data.get("content"), str):
        raise ValueError("全本小说网的搜索格式已改变。")
    return data["content"]
