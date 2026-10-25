"""Install the Android build prerequisites into the existing local dev-tools folder."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

DEV = Path('E:/dev-tools')
SDK = DEV / 'android-sdk'
DOWNLOADS = DEV / 'downloads'
DOWNLOADS.mkdir(parents=True, exist_ok=True)

def download(urls, target):
    if target.is_file():
        try:
            with zipfile.ZipFile(target) as archive:
                if archive.testzip() is None:
                    print(f'Cached {target.name}', flush=True)
                    return target
        except zipfile.BadZipFile:
            pass
    for url in urls:
        try:
            print(f'Downloading {url}', flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, target.open('wb') as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            with zipfile.ZipFile(target) as archive:
                assert archive.testzip() is None
            print(f'Downloaded {target.name}: {target.stat().st_size} bytes', flush=True)
            return target
        except Exception as error:
            print(f'Download retry: {type(error).__name__}: {error}', flush=True)
    raise RuntimeError(f'Unable to download {target.name}')

def android_tools():
    manager = SDK / 'cmdline-tools/latest/bin/sdkmanager.bat'
    if manager.is_file():
        return manager
    with urllib.request.urlopen('https://dl.google.com/android/repository/repository2-3.xml', timeout=45) as response:
        xml = ET.fromstring(response.read())
    packages = [node for node in xml if node.tag.endswith('remotePackage') and node.attrib.get('path', '').startswith('cmdline-tools;')]
    choices = []
    for package in packages:
        for archive in package.findall('./archives/archive'):
            if archive.findtext('host-os') == 'windows':
                revision = tuple(int(package.findtext(f'revision/{name}') or 0) for name in ('major','minor','micro'))
                choices.append((revision, archive.findtext('complete/url')))
    _, name = max(choices)
    archive = download(['https://dl.google.com/android/repository/' + name], DOWNLOADS / name)
    staging = SDK / 'bootstrap-commandline'
    with zipfile.ZipFile(archive) as package:
        package.extractall(staging)
    shutil.copytree(staging / 'cmdline-tools', SDK / 'cmdline-tools/latest', dirs_exist_ok=True)
    return manager

def gradle():
    executable = DEV / 'gradle-8.11.1/bin/gradle.bat'
    if executable.is_file():
        return executable
    name = 'gradle-8.11.1-bin.zip'
    archive = download(['https://downloads.gradle.org/distributions/' + name,
                        'https://services.gradle.org/distributions/' + name], DOWNLOADS / name)
    with zipfile.ZipFile(archive) as package:
        package.extractall(DEV)
    return executable

if __name__ == '__main__':
    if '--sdk-only' in sys.argv:
        manager = android_tools()
        gradle_exe = DEV / 'gradle-8.11.1/bin/gradle.bat'
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sdk_future = pool.submit(android_tools)
            gradle_future = pool.submit(gradle)
            manager = sdk_future.result()
            gradle_exe = gradle_future.result()
    environment = os.environ.copy()
    environment['ANDROID_HOME'] = environment['ANDROID_SDK_ROOT'] = str(SDK)
    environment['ANDROID_USER_HOME'] = str(DEV / 'android-user')
    log = DEV / 'setup-records/bookpack-android-sdk.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w', encoding='utf-8') as output:
        subprocess.run([str(manager), '--sdk_root=' + str(SDK), '--licenses'], input='y\n'*30,
                       text=True, stdout=output, stderr=subprocess.STDOUT, env=environment, check=True)
        native_manager = manager.with_name('android.exe')
        if native_manager.is_file():
            # Recent Windows sdkmanager.bat shims split semicolon package names.
            for package in ('platform-tools', 'platforms/android-35', 'build-tools/35.0.0'):
                subprocess.run([str(native_manager), '--sdk=' + str(SDK), 'sdk', '--ignore-outdated-xmls', 'install', package],
                               stdout=output, stderr=subprocess.STDOUT, env=environment, check=True)
        else:
            subprocess.run([str(manager), '--sdk_root=' + str(SDK), 'platform-tools', 'platforms;android-35', 'build-tools;35.0.0'],
                           stdout=output, stderr=subprocess.STDOUT, env=environment, check=True)
    for required in ('platform-tools/adb.exe', 'platforms/android-35/android.jar', 'build-tools/35.0.0/aapt.exe'):
        if not (SDK / required).is_file():
            raise RuntimeError(f'SDK installation is incomplete: {required}; see {log}')
    print(json.dumps({'sdk': str(SDK), 'gradle': str(gradle_exe), 'log': str(log)}), flush=True)
