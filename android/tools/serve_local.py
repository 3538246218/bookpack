"""Exercise the Android HTTP adapter with isolated state on the development host."""
from pathlib import Path
import argparse
import sys
import time

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--home',type=Path,required=True)
args = parser.parse_args()
sys.path.insert(0,str(project / 'app/src/main/python'))
from android_runtime import start
print(start(project / 'app/src/main/assets/runtime',args.home),flush=True)
try:
    while True: time.sleep(.2)
except KeyboardInterrupt:
    pass
