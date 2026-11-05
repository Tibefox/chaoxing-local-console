"""Build with the already installed Android SDK; no Gradle/network downloads."""
import os
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent
BUILD=ROOT.parent/'runtime/android-build'
SDK=Path(r'D:\PhotoDiarySdk');BT=SDK/'build-tools/36.0.0';JAR=SDK/'platforms/android-36/android.jar'
JAVA=Path(os.environ['JAVA_HOME'])/'bin'
def run(args):subprocess.run([str(a) for a in args],check=True,cwd=ROOT)
def main():
    BUILD.mkdir(parents=True,exist_ok=True);classes=BUILD/'classes';classes.mkdir(exist_ok=True)
    run([JAVA/'javac.exe','-encoding','UTF-8','--release','8','-classpath',JAR,'-d',classes,*ROOT.glob('src/**/*.java')])
    run([BT/'d8.bat','--lib',JAR,'--min-api','26','--output',BUILD,*classes.glob('**/*.class')])
    apk=BUILD/'course-notifier-unsigned.apk'
    run([BT/'aapt.exe','package','-f','-M',ROOT/'AndroidManifest.xml','-I',JAR,'-F',apk])
    with zipfile.ZipFile(apk,'a') as z:z.write(BUILD/'classes.dex','classes.dex')
    aligned=BUILD/'course-notifier-aligned.apk';run([BT/'zipalign.exe','-f','4',apk,aligned])
    key=BUILD/'local-debug.keystore'
    if not key.exists():run([JAVA/'keytool.exe','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','localdebug','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=Local Course Notification Bridge'])
    target=BUILD/'course-notifier.apk';run([BT/'apksigner.bat','sign','--ks',key,'--ks-pass','pass:android','--key-pass','pass:android','--out',target,aligned])
    run([BT/'apksigner.bat','verify',target]);print(target)
if __name__=='__main__':main()
