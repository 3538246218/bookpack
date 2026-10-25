"""Package application sources, excluding local tasks and downloaded books."""
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[1]
files = ["app.py", "bookpack_core.py", "bookpack_search.py", "bookpack_sources.py", "bookpack_content.py", "bookpack_match.py", "bookpack_suggest.py", "bookpack_platforms.py", "data/platform_catalog.json", "web/index.html", "web/app.css", "web/app.js", "web/search-suggestions.js", "web/ocean-controls.js",
         "启动书包.cmd", "启动书包.ps1", "README.md", "示例清单.csv", "示例链接清单.csv", "书源目录示例.json", "验证说明.md",
         "在线书源说明.md", "tests/test_functional.py", "tests/test_sources.py", "tests/test_matching.py", "tests/test_suggestions.py", "tests/test_platforms.py", "预览/桌面.png", "预览/窄屏.png",
         "预览/结果.png", "预览/窄屏结果.png", "预览/海面调节.png", "预览/夕阳.png", "预览/窄屏调节.png",
         "预览/候选书籍.png", "预览/候选书籍窄屏.png", "预览/候选获取结果.png", "预览/自动匹配.png",
         "预览/搜索补全.png", "预览/搜索纠错.png", "预览/搜索抽屉窄屏.png", "预览/抽屉滚动.png",
         "预览/平台归属.png", "预览/番茄平台.png", "预览/平台归属窄屏.png"]
files.extend(["tests/test_download_reliability.py", "tools/inspect_downloads.py", "tools/repair_known_chapters.py"])
files.extend(["tests/test_download_speed.py", "加速验证.md"])
files.extend(["bookpack_bulk.py", "tests/test_bulk_download.py", "批量抓取验证.md"])
files.extend(["bookpack_lightnovel.py", "data/lightnovel_catalog.json", "tests/test_lightnovel.py", "tools/update_lightnovel_catalog.py", "轻小说接入说明.md"])
files.extend(["bookpack_source_rules.py", "tests/test_source_expansion.py", "扩源验证.md"])
files.extend(["bookpack_radar.py", "web/radar.js", "web/radar.css", "tests/test_radar.py", "tools/radar_smoke.mjs", "番茄雷达接入说明.md",
              "vendor/__init__.py", "预览/番茄雷达.png", "预览/番茄雷达窄屏.png"])
files.extend(["web/feedback.js", "web/feedback.css", "交互反馈说明.md"])
files.extend("预览/" + name for name in ("反馈首次加载.png", "反馈筛选等待.png", "反馈选书衔接.png", "反馈搜索等待.png", "反馈获取完成.png"))
files.extend(path.relative_to(root).as_posix() for path in sorted((root / "vendor/fanqie_radar").iterdir()) if path.is_file())
files.extend(path.relative_to(root).as_posix() for path in sorted((root / "data/radar").glob("*.json")))
files.extend(["预览/下载缺损提示.png", "预览/下载缺损窄屏.png"])
files.extend("预览/" + name for name in ("天空搜索.png", "天空抽屉.png", "天空抽屉窄屏.png", "横屏抽屉.png", "紧凑横屏抽屉.png", "自动匹配改选.png", "改选完成.png"))
files.extend(path.relative_to(root).as_posix() for path in sorted((root / "web/ocean").rglob("*")) if path.is_file())
target = root / "书包-本地版-1.8.2.zip"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in files:
        archive.write(root / name, "书包/" + name)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(files)
print(target)
print(f"{len(files)} files, {target.stat().st_size} bytes")
