import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpack_platforms import WorkPlatforms
from bookpack_radar import RadarError, RadarService
from bookpack_search import normalize, parse_titles


def book(bid, title, author="甲作者", reads=100000, status="连载中"):
    return {"bookid": str(bid), "title": title, "author": author, "reads": reads,
            "url": "https://fanqienovel.com/page/" + str(bid), "status": status}


def snapshot(directory, channel, rank, date, categories):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"fanqie_{channel}_{rank}_ranks_{date}.json"
    target.write_text(json.dumps({"date": date, "categories": [
        {"name": name, "books": books} for name, books in categories]}, ensure_ascii=False), encoding="utf-8")
    return target


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.bundle = self.home / "bundled"
        self.radar = RadarService(self.home, bundled=self.bundle)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.radar.close)

    def seed(self):
        snapshot(self.bundle, "male", "read", "20261013", [
            ("玄幻", [book(1, "同名书", "甲作者", 500000), book(2, "同名书", "乙作者", 120000, "完结")]),
            ("都市", [book(3, "都市原创", reads=50000)])])
        snapshot(self.bundle, "female", "new", "20261013", [("古言", [book(4, "女频原创")])])

    def test_filters_keep_board_categories_and_correct_author(self):
        self.seed()
        result = self.radar.browse({"channel": "male", "rank": "read", "q": "乙作者", "status": "done", "min_reads": "100000"})
        self.assertEqual(result["date"], "20261013")
        self.assertEqual(result["categories"], ["玄幻", "都市"])
        self.assertEqual([(row["book_id"], row["author"]) for row in result["items"]], [("2", "乙作者")])
        self.assertEqual(self.radar.browse({"channel": "female", "rank": "new"})["items"][0]["book_id"], "4")
        self.assertEqual(self.radar.browse({"q": "没有这本"})["total"], 0)
        with self.assertRaises(RadarError):
            self.radar.browse({"channel": "other"})

    def test_user_snapshot_precedence_and_trends_are_per_category(self):
        self.seed()
        snapshot(self.radar.directory, "male", "read", "20261014", [
            ("玄幻", [book(2, "同名书", "乙作者", 600000), book(1, "同名书", "甲作者", 500000)]),
            ("都市", [book(5, "新上榜")])])
        result = self.radar.browse({"sort": "pos"})
        indexed = {row["book_id"]: row for row in result["items"]}
        self.assertEqual(result["previous_date"], "20261013")
        self.assertEqual(indexed["2"]["rank_change"], 1)
        self.assertEqual(indexed["1"]["rank_change"], -1)
        self.assertTrue(indexed["5"]["new_entry"])
        # Replacing the same filename is detected without restarting the service.
        snapshot(self.radar.directory, "male", "read", "20261014", [("都市", [book(6, "替换快照")])])
        self.assertEqual(self.radar.browse({})["items"][0]["book_id"], "6")

    def test_select_passes_exact_title_author_and_platform_to_search(self):
        self.seed()
        self.radar.platforms = WorkPlatforms(self.home, normalize, catalog_file=self.home / "absent.json")
        selected = self.radar.select("2", "male", "read")
        parsed = parse_titles(selected["title"] + " | " + selected["author"])
        self.assertEqual(parsed[0]["author"], "乙作者")
        work = self.radar.platforms.annotate(selected)
        self.assertEqual(work["platform_label"], "番茄")
        self.assertEqual(work["platform_category"], "玄幻")
        with self.assertRaises(RadarError):
            self.radar.select("999", "male", "read")

    def test_refresh_failure_does_not_replace_a_saved_snapshot(self):
        self.seed()
        before = self.radar.browse({})
        with patch.object(self.radar, "_fetch_state", side_effect=RadarError("页面暂不可用")):
            self.radar._refresh("male", "read")
        self.assertFalse(self.radar.status()["running"])
        self.assertIn("页面暂不可用", self.radar.status()["error"])
        self.assertEqual(self.radar.browse({})["items"], before["items"])
        self.assertFalse(self.radar.directory.exists())

    def test_current_page_categories_and_numeric_completed_status(self):
        raw_a = {"bookId": "31", "bookName": "都市原创", "author": "丙作者", "readCount": 330000, "creationStatus": 0}
        raw_b = {"bookId": "32", "bookName": "奇幻原创", "author": "丁作者", "readCount": 120000, "creationStatus": 1}
        # The entry category is intentionally not first in the site's category list.
        initial = {"rank": {"rankCategoryTypeList": {"male": [
            {"id": "261", "name": "都市"}, {"id": "1141", "name": "奇幻"}]}, "book_list": [raw_b]}}
        def fetch(url):
            return "", initial if url.endswith("1141") else {"rank": {"book_list": [raw_a]}}
        with patch.object(self.radar, "_fetch_state", side_effect=fetch), patch.object(self.radar.stop, "wait", return_value=False):
            self.radar._refresh("male", "read")
        self.assertEqual(self.radar.status()["error"], "")
        self.assertEqual(self.radar.status()["completed"], 2)
        result = self.radar.browse({})
        self.assertEqual(result["date"], datetime.now().astimezone().strftime("%Y%m%d"))
        indexed = {row["book_id"]: row for row in result["items"]}
        self.assertEqual((indexed["31"]["category"], indexed["31"]["status"]), ("都市", "完结"))
        self.assertEqual(indexed["32"]["category"], "奇幻")
        self.assertEqual(len(list(self.radar.directory.glob("*.json"))), 1)

    def test_refresh_requests_share_one_background_job(self):
        entered, release = threading.Event(), threading.Event()
        def refresh(channel, rank):
            entered.set()
            release.wait(3)
            self.radar._update(running=False)
        with patch.object(self.radar, "_refresh", side_effect=refresh) as worker:
            first = self.radar.start_refresh("male", "read")
            self.assertTrue(entered.wait(1))
            second = self.radar.start_refresh("female", "new")
            self.assertTrue(first["running"])
            self.assertEqual(second["channel"], "male")
            release.set()
            self.radar.worker.join(2)
            self.assertEqual(worker.call_count, 1)


if __name__ == "__main__":
    unittest.main()
