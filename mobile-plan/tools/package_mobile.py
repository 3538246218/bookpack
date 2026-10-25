"""Package the plan and runnable prototype, excluding runtime and private data."""
from pathlib import Path
import zipfile

plan = Path(__file__).resolve().parents[1]
files = [plan / name for name in ("移动端迁移方案.md", "NAVIGATION.md", "SITEMAP.mmd", "验证记录.md", "validation.json", "预览手机版.cmd", "预览手机版.ps1", "tools/serve.py", "tools/mobile_smoke.mjs")]
files += [path for directory in ("prototype", "previews") for path in sorted((plan / directory).rglob("*")) if path.is_file()]
target = plan.parent / "书包-移动端方案与原型.zip"
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for file in files:
        archive.write(file, "书包移动端方案/" + file.relative_to(plan).as_posix())
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(files)
print(str(target))
print(f"{len(files)} files, {target.stat().st_size} bytes")
