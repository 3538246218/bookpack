"""Fetch the pinned reader's official compile-time Cronet JARs."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import urllib.request

project = Path(__file__).resolve().parents[1]
version = '152.0.7977.54'
names = ['cronet_api.jar', 'cronet_impl_common_java.jar', 'cronet_impl_native_java.jar',
         'cronet_impl_native_sentinel_java.jar', 'cronet_impl_platform_java.jar',
         'cronet_shared_java.jar', 'httpengine_native_provider_java.jar']
directory = project / 'legado/cronetlib'
directory.mkdir(parents=True, exist_ok=True)

def fetch(name):
    target = directory / name
    if not target.exists():
        temporary = target.with_suffix('.part')
        urllib.request.urlretrieve('https://storage.googleapis.com/chromium-cronet/android/' +
                                   version + '/Release/cronet/' + name, temporary)
        temporary.replace(target)
    return {'name': name, 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        jars = list(pool.map(fetch, names))
    print(json.dumps({'version': version, 'jars': jars}))
