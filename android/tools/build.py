"""Build in an ASCII checkout for the Windows Android toolchain."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

source = Path(__file__).resolve().parents[1]
subprocess.check_call([sys.executable, '-X', 'utf8', str(source / 'tools/prepare_legado.py')])
build = Path('E:/dev-tools/builds/bookpack-android')
def copy_changed(original, target):
    original, target = Path(original), Path(target)
    if not target.exists() or (original.stat().st_size, original.stat().st_mtime_ns) != (target.stat().st_size, target.stat().st_mtime_ns):
        shutil.copy2(original, target)
    return str(target)
shutil.copytree(source, build, dirs_exist_ok=True, copy_function=copy_changed,
                ignore=shutil.ignore_patterns('.gradle','build','outputs','__pycache__','.signing'))
(build / 'local.properties').write_text('sdk.dir=E:/dev-tools/android-sdk\n', encoding='ascii')
environment = os.environ.copy()
environment['ANDROID_HOME'] = environment['ANDROID_SDK_ROOT'] = 'E:/dev-tools/android-sdk'
environment['ANDROID_USER_HOME'] = 'E:/dev-tools/android-user'
environment['GRADLE_USER_HOME'] = 'E:/dev-tools/gradle-cache'
environment['PYTHONUTF8'] = '1'
command = ['E:/dev-tools/gradle-8.14.4/bin/gradle.bat', '--console=plain', '--no-daemon', '-PbookpackPython=' + sys.executable]
command += sys.argv[1:] or [':app:assembleDebug']
raise SystemExit(subprocess.call(command, cwd=build, env=environment))
