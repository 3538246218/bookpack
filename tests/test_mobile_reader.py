import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import demo_epub
from bookpack_core import Engine
from bookpack_reader import ReaderLibrary, ReaderError
from bookpack_radar import RadarService
from vendor.fanqie_radar import scraper


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.engine = Engine(self.home)
        self.library = ReaderLibrary(self.engine)
        self.books = self.home / '本地书库'
        self.books.mkdir()

    def test_txt_progress_bookmarks_search_survive_restart(self):
        (self.books / '山间来信.txt').write_bytes('第一章 雨声\n窗外的雨停了，信使来到山间。\n第二章 回信\n他在信纸上写下新的约定。'.encode('gb18030'))
        identity = self.library.list()['books'][0]['id']
        book = self.library.book(identity)
        self.assertEqual([row['title'] for row in book['chapters']], ['第一章 雨声', '第二章 回信'])
        self.assertIn('信使', self.library.chapter(identity, 0)['text'])
        saved = self.library.save({'id': identity, 'chapter': 1, 'offset': .4, 'action': 'bookmark'})
        reopened = ReaderLibrary(Engine(self.home))
        self.assertEqual(reopened.book(identity)['progress']['chapter'], 1)
        self.assertEqual(reopened.book(identity)['progress']['offset'], .4)
        self.assertEqual(reopened.search(identity, '约定')['matches'][0]['chapter'], 1)
        reopened.save({'id': identity, 'chapter': 1, 'offset': .4, 'action': 'remove_bookmark', 'bookmark_id': saved['bookmarks'][0]['id']})
        self.assertEqual(reopened.book(identity)['progress']['bookmarks'], [])

    def test_epub_spine_text_and_replaced_file_cache(self):
        path = self.books / '原创.epub'
        path.write_bytes(demo_epub())
        identity = self.library.list()['books'][0]['id']
        self.assertIn('银杏叶', self.library.chapter(identity, 0)['text'])
        self.assertNotIn('xmlns', self.library.chapter(identity, 0)['text'])
        with self.assertRaises(ReaderError):
            self.library.book('does-not-exist')


class DailyRadarTests(unittest.TestCase):
    def test_official_javascript_undefined_does_not_break_rank_state(self):
        html = '<script>window.__INITIAL_STATE__={"rank":{"book_list":[{"bookName":"undefined 保留原书名","pos_category_id":undefined}]}};window.other=true;</script>'
        book = scraper._extract_books(scraper._parse_state(html))[0]
        self.assertEqual(book['bookName'], 'undefined 保留原书名')
        self.assertIsNone(book['pos_category_id'])

    def test_current_field_and_partial_category_do_not_discard_good_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            radar = RadarService(home, bundled=home / 'empty')
            initial = {'rank': {'rankCategoryTypeList': {'male': [{'id': '1141', 'name': '奇幻'}, {'id': '8', 'name': '科幻'}]},
                'book_list': [{'bookId': '33', 'bookName': '山间来信', 'author': '书包', 'readCount': '0', 'read_count': '123456', 'creationStatus': 0, 'lastChapterTitle': '第三章 约定'}]}}
            def fetch(url):
                if url.endswith('1141'):
                    return '', initial
                raise ValueError('该分类网络暂不可用')
            with patch.object(radar, '_fetch_state', side_effect=fetch), patch.object(radar.stop, 'wait', return_value=False):
                radar._refresh('male', 'read')
            result = radar.browse({})
            self.assertEqual(result['items'][0]['reads'], 123456)
            self.assertEqual(result['items'][0]['status'], '完结')
            self.assertEqual(result['items'][0]['last_chapter'], '第三章 约定')
            self.assertEqual(result['failed_categories'], ['科幻'])
            self.assertIn('科幻', radar.status()['error'])

    def test_daily_switch_is_persisted_and_prevents_automatic_network(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            radar = RadarService(home)
            radar.configure_daily(False)
            reopened = RadarService(home)
            with patch.object(reopened, '_daily_refresh') as refresh:
                self.assertFalse(reopened.start_daily()['running'])
                refresh.assert_not_called()
            self.assertFalse(json.loads((home / '雷达更新状态.json').read_text())['enabled'])


if __name__ == '__main__':
    unittest.main()
