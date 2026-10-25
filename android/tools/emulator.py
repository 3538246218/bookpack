"""Start the task-owned, headless Android 35 test device."""
from pathlib import Path
import json
import os
import subprocess

sdk = Path('E:/dev-tools/android-sdk')
avds = Path('E:/dev-tools/android-avd')
directory = avds / 'BookpackApi35.avd'
image = sdk / 'system-images/android-35/google_apis/x86_64'
if not (image / 'source.properties').is_file():
    raise SystemExit('Android 35 system image is not installed yet.')
directory.mkdir(parents=True, exist_ok=True)
(avds / 'BookpackApi35.ini').write_text('avd.ini.encoding=UTF-8\npath=' + str(directory) + '\ntarget=android-35\n',encoding='utf-8')
config = {'AvdId':'BookpackApi35','avd.ini.encoding':'UTF-8','abi.type':'x86_64','hw.cpu.arch':'x86_64',
          'hw.cpu.ncore':'2','hw.ramSize':'2048','hw.lcd.width':'1080','hw.lcd.height':'2400','hw.lcd.density':'420',
          'hw.gpu.enabled':'yes','hw.gpu.mode':'swiftshader_indirect','hw.keyboard':'no','hw.mainKeys':'no',
          'disk.dataPartition.size':'2G','image.sysdir.1':image.as_posix()+'/',
          'tag.id':'google_apis','tag.display':'Google APIs','PlayStore.enabled':'false','showDeviceFrame':'no'}
(directory / 'config.ini').write_text('\n'.join(key+'='+value for key,value in config.items())+'\n',encoding='utf-8')
environment=os.environ.copy();environment['ANDROID_AVD_HOME']=str(avds);environment['ANDROID_SDK_ROOT']=str(sdk);environment['ANDROID_USER_HOME']='E:/dev-tools/android-user'
log=Path(__file__).resolve().parents[1] / 'outputs/emulator.log';log.parent.mkdir(parents=True,exist_ok=True)
with log.open('w',encoding='utf-8') as output:
    process=subprocess.Popen([str(sdk/'emulator/emulator.exe'),'-avd','BookpackApi35','-no-window','-no-audio','-no-boot-anim','-no-snapshot','-gpu','swiftshader_indirect','-memory','2048','-cores','2','-port','5554'],env=environment,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
print(json.dumps({'pid':process.pid,'serial':'emulator-5554','log':str(log)}),flush=True)
