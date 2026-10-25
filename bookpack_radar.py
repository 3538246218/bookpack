"""Fanqie Radar adapter: dated rank snapshots, browsing and background refresh."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

from bookpack_platforms import official_record
from vendor.fanqie_radar import analysis, schema, scraper

BUNDLED_DATA = Path(__file__).resolve().parent / "data" / "radar"
SNAPSHOT_NAME = re.compile(r"fanqie_(female|male)_(read|new)_ranks_(\d{8})\.json$")


class RadarError(ValueError):
    pass


def board_key(channel, rank):
    if channel not in {"female", "male"} or rank not in {"read", "new"}:
        raise RadarError("请选择男频或女频，以及阅读榜或新书榜。")
    return channel, rank


class RadarService:
    def __init__(self, home, platforms=None, bundled=BUNDLED_DATA):
        self.directory = Path(home) / "番茄雷达" / "榜单"
        self.bundled = Path(bundled)
        self.platforms = platforms
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        self._signature = None
        self._cache = {}
        self._warnings = []
        self.settings_file = Path(home) / '雷达更新状态.json'
        try:
            self.daily = json.loads(self.settings_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.daily = {'enabled': True, 'last_attempt': '', 'last_success': '', 'boards': {}}
        self._status = {"running": False, "channel": "", "rank": "", "completed": 0,
                        "total": 0, "books": 0, "message": "", "error": "", "updated_at": ""}

    def _boards(self):
        # User snapshots take precedence over bundled files of the same date.
        files = {}
        for directory in (self.directory, self.bundled):
            for path in sorted(directory.glob("fanqie_*_ranks_*.json")):
                if SNAPSHOT_NAME.fullmatch(path.name):
                    files.setdefault(path.name, path)
        signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                          for path in files.values())
        with self.lock:
            if signature == self._signature:
                return self._cache, self._warnings
            boards, warnings = {}, []
            for name, path in sorted(files.items()):
                channel, rank, date = SNAPSHOT_NAME.fullmatch(name).groups()
                try:
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    books = [book for book in schema.load_books(path) if book.get("title")]
                    if not books:
                        raise ValueError("榜单为空")
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    warnings.append(f"未读取 {name}：{exc}")
                    continue
                boards.setdefault((channel, rank), []).append({"date": date, "books": books,
                    'fetched_at': payload.get('fetched_at', ''), 'failed_categories': payload.get('failed_categories', [])})
            for snapshots in boards.values():
                snapshots.sort(key=lambda row: row["date"])
            self._cache, self._warnings, self._signature = boards, warnings, signature
            return boards, warnings

    @staticmethod
    def _identity(book):
        return book.get("category", ""), book.get("book_id") or (book["title"], book.get("author", ""))

    def browse(self, query):
        channel, rank = board_key(query.get("channel", "male"), query.get("rank", "read"))
        boards, warnings = self._boards()
        history = boards.get((channel, rank), [])
        current = history[-1] if history else {"date": "", "books": []}
        previous = history[-2] if len(history) > 1 else None
        books = current["books"]
        categories = list(dict.fromkeys(book.get("category") or "未分类" for book in books))
        category = str(query.get("category") or "")
        keyword = str(query.get("q") or "").strip().casefold()
        status = query.get("status", "")
        try:
            minimum = max(0, int(query.get("min_reads") or 0))
            offset = max(0, int(query.get("offset") or 0))
            limit = max(1, min(60, int(query.get("limit") or 30)))
        except (ValueError, TypeError) as exc:
            raise RadarError("筛选数量需要填写整数。") from exc
        filtered = [book for book in books
                    if (not category or (book.get("category") or "未分类") == category)
                    and (not keyword or keyword in (book["title"] + " " + book.get("author", "")).casefold())
                    and int(book.get("reads") or 0) >= minimum
                    and (not status or status == "done" and "完结" in book.get("status", "")
                         or status == "serial" and "连载" in book.get("status", ""))]
        if query.get("sort") == "pos":
            filtered.sort(key=lambda book: (book["rank_pos"], -book["reads"]))
        else:
            filtered.sort(key=lambda book: (-book["reads"], book["rank_pos"]))
        old = {self._identity(book): book for book in previous["books"]} if previous else {}
        items = []
        for book in filtered[offset:offset + limit]:
            item = {key: book.get(key) for key in ("book_id", "title", "author", "category", "reads",
                                                   "reads_raw", "status", "chapters", "url", "rank_pos",
                                                   'last_chapter', 'last_updated', 'word_count', 'stale', 'observed_at')}
            prior = old.get(self._identity(book))
            item["new_entry"] = bool(previous and not prior)
            item["rank_change"] = prior["rank_pos"] - book["rank_pos"] if prior else None
            items.append(item)
        return {"available": bool(books), "channel": channel, "rank": rank,
                "date": current["date"], "previous_date": previous["date"] if previous else "",
                'fetched_at': current.get('fetched_at', ''), 'failed_categories': current.get('failed_categories', []),
                'stale': current['date'] != datetime.now().astimezone().strftime('%Y%m%d'),
                "categories": categories, "total": len(filtered), "board_total": len(books),
                "offset": offset, "limit": limit, "items": items,
                "heat": analysis.category_heat(books)[:6],
                "hotwords": analysis.hotwords(books, top=8), "warnings": warnings,
                "refresh": self.status()}

    def select(self, book_id, channel, rank):
        channel, rank = board_key(channel, rank)
        boards, _ = self._boards()
        history = boards.get((channel, rank), [])
        book = next((row for row in history[-1]["books"] if row["book_id"] == str(book_id)), None) if history else None
        if not book:
            raise RadarError("这本书已不在当前榜单，请刷新列表后再选。")
        self._observe([book])
        return {"title": book["title"], "author": book.get("author", ""), "book_id": book["book_id"]}

    def _observe(self, books):
        if self.platforms is not None:
            self.platforms.observe([{**book, "platform_record": official_record(
                book["title"], book.get("author", ""), "番茄", book.get("url", ""),
                book.get("category", ""), evidence="official_rank")} for book in books])

    def status(self):
        with self.lock:
            return {**self._status, 'daily': dict(self.daily)}

    def _save_daily(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.daily, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.settings_file)

    def configure_daily(self, enabled):
        with self.lock:
            self.daily['enabled'] = bool(enabled)
            if not enabled and self._status.get('automatic'):
                self.stop.set()
            self._save_daily()
        return self.status()

    def start_daily(self, force=False):
        today = datetime.now().astimezone().strftime('%Y%m%d')
        with self.lock:
            if self._status['running'] or not force and not self.daily.get('enabled', True):
                return self.status()
            boards, _ = self._boards()
            pairs = [(channel, rank) for channel in ('male', 'female') for rank in ('read', 'new')
                     if force or not boards.get((channel, rank)) or boards[channel, rank][-1]['date'] != today
                     or boards[channel, rank][-1].get('failed_categories')]
            if not pairs:
                now = datetime.now().astimezone().isoformat()
                self.daily.update(last_attempt=now, last_success=now)
                self._save_daily()
                return self.status()
            if not force and self.daily.get('last_attempt'):
                try:
                    elapsed = (datetime.now().astimezone() - datetime.fromisoformat(self.daily['last_attempt'])).total_seconds()
                    if elapsed < 15 * 60:
                        return self.status()
                except (ValueError, TypeError):
                    pass
            self.stop.clear()
            self._status.update(running=True, error='', message='正在检查每日榜单更新…', automatic=True)
            self.daily['last_attempt'] = datetime.now().astimezone().isoformat()
            self._save_daily()
            self.worker = threading.Thread(target=self._daily_refresh, args=(pairs,), daemon=True, name='bookpack-radar-daily')
            self.worker.start()
            return self.status()

    def _daily_refresh(self, pairs):
        errors = []
        try:
            for channel, rank in pairs:
                if self.stop.is_set():
                    break
                self._update(channel=channel, rank=rank, completed=0, total=0, books=0, error='')
                self._refresh(channel, rank, finalize=False)
                if self._status['error']:
                    errors.append(self._status['error'])
            if not errors and not self.stop.is_set():
                with self.lock:
                    self.daily['last_success'] = datetime.now().astimezone().isoformat()
                    self._save_daily()
            self._update(error='；'.join(errors), message='每日榜单检查完成。' if not errors else '部分榜单未更新，已保留可用数据。')
        finally:
            self._update(running=False, automatic=False)

    def _update(self, **values):
        with self.lock:
            self._status.update(values)

    def start_refresh(self, channel="male", rank="read"):
        channel, rank = board_key(channel, rank)
        with self.lock:
            if self._status["running"]:
                return dict(self._status)
            self.stop.clear()
            self._status = {"running": True, "channel": channel, "rank": rank,
                            "completed": 0, "total": 0, "books": 0, "error": "", "updated_at": "",
                            "message": "正在连接番茄公开榜单…"}
            self.worker = threading.Thread(target=self._refresh, args=(channel, rank), daemon=True,
                                           name="bookpack-radar-refresh")
            self.worker.start()
            return dict(self._status)

    def _fetch_state(self, url):
        for attempt in range(3):
            if self.stop.is_set():
                raise RadarError("榜单更新已停止。")
            try:
                html = scraper._fetch(url)
                state = scraper._parse_state(html)
                if not state or not isinstance(state.get("rank"), dict):
                    raise RadarError("番茄未返回可解析的榜单，请稍后重试。")
                return html, state
            except Exception:
                if attempt == 2 or self.stop.wait(attempt + 1):
                    raise

    def _refresh(self, channel, rank, finalize=True):
        try:
            gender, kind = (1 if channel == "male" else 0), (2 if rank == "read" else 1)
            config = next(row for row in scraper.RANK_CONFIGS if row["gender"] == gender and row["type"] == kind)
            base = f"https://fanqienovel.com/rank/{gender}_{kind}_"
            html, initial = self._fetch_state(base + config["entry_cat"])
            # Current official pages group id/name pairs by channel in this field.
            grouped = initial["rank"].get("rankCategoryTypeList") or {}
            categories = [{"id": str(row["id"]), "name": str(row["name"])}
                          for row in grouped.get(channel, []) if row.get("id") and row.get("name")]
            if not categories:
                categories = scraper._extract_categories(initial, gender, kind)
            if not categories:
                categories = [{"id": cid, "name": name.strip()} for cid, name in re.findall(
                    rf'href="/rank/{gender}_{kind}_(\d+)"[^>]*>([^<]+)</a>', html)]
            categories = list({str(row["id"]): row for row in categories}.values())
            if not categories:
                raise RadarError("没有读取到榜单分类，原榜单已保留。")
            self._update(total=len(categories))
            collected, total, failed = [], 0, []
            boards, _ = self._boards()
            previous = boards.get((channel, rank), [])
            old = previous[-1]['books'] if previous else []
            fetched_at = datetime.now().astimezone().isoformat()
            for index, category in enumerate(categories):
                if self.stop.is_set():
                    raise RadarError("榜单更新已停止。")
                self._update(message=f"正在更新{config['name']} · {category['name']}（{index + 1}/{len(categories)}）")
                try:
                    if str(category['id']) == config['entry_cat']:
                        state = initial
                    else:
                        if self.stop.wait(.35):
                            raise RadarError('榜单更新已停止。')
                        _, state = self._fetch_state(base + str(category['id']))
                    raw = scraper._extract_books(state)
                    if not raw and state['rank'].get('total_num') not in (0, '0'):
                        raise RadarError('没有读取到书目')
                except Exception as exc:
                    if self.stop.is_set():
                        raise RadarError('榜单更新已停止。') from exc
                    failed.append(category['name'])
                    fallback = [dict(book, bookid=book['book_id'], stale=True) for book in old if book['category'] == category['name']]
                    collected.append({'name': category['name'], 'books': fallback})
                    total += len(fallback)
                    self._update(completed=index + 1, books=total)
                    continue
                normalized = []
                for row in raw[:30]:
                    book = scraper._normalize_book(row, category["name"])
                    # The current site returns a placeholder readCount="0" alongside real read_count.
                    reads = row.get('read_count') or row.get('reading_count') or row.get('readCount') or '0'
                    book.update(reads=scraper.decode_text(str(reads)), observed_at=fetched_at, stale=False,
                        last_chapter=scraper.decode_text(str(row.get('lastChapterTitle') or '')),
                        last_updated=str(row.get('lastChapterUpdateTime') or ''), word_count=str(row.get('wordNumber') or ''))
                    # Upstream's truthy lookup drops the legitimate numeric status 0.
                    creation = row.get("creationStatus", row.get("creation_status"))
                    if creation is not None and str(creation) in {"0", "1"}:
                        book["status"] = "完结" if str(creation) == "0" else "连载中"
                    if not book["title"] or not book["bookid"]:
                        raise RadarError(f"“{category['name']}”的书目格式已变化，原榜单已保留。")
                    normalized.append(book)
                collected.append({"name": category["name"], "books": normalized})
                total += len(normalized)
                self._update(completed=index + 1, books=total)
            if self.stop.is_set():
                raise RadarError("榜单更新已停止。")
            date = datetime.now().astimezone().strftime("%Y%m%d")
            if len(failed) == len(categories):
                raise RadarError('所有分类暂时无法读取，原榜单已保留。')
            payload = {"date": date, "rank_type": config["name"], "categories": collected,
                       'fetched_at': fetched_at, 'failed_categories': failed}
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / f"fanqie_{channel}_{rank}_ranks_{date}.json"
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            # Load the exact file to be published before replacing an existing snapshot.
            books = schema.load_books(temporary)
            if len(books) != total:
                raise RadarError("榜单保存未完成，原榜单已保留。")
            temporary.replace(destination)
            self._observe(schema.load_books(destination))
            with self.lock:
                self.daily.setdefault('boards', {})[channel + '/' + rank] = {'date': date, 'fetched_at': fetched_at, 'partial': bool(failed)}
                self._save_daily()
            self._update(message=f"{config['name']}已更新，{len(collected)} 个分类、{total} 条记录。",
                         updated_at=date, error=('以下分类未更新：' + '、'.join(failed)) if failed else '')
        except Exception as exc:
            self._update(error=str(exc), message="更新未完成，继续显示已保存的榜单。")
        finally:
            if finalize:
                self._update(running=False)

    def close(self):
        self.stop.set()
