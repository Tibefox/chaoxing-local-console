"""Build the local foreground reader with the project's existing Android toolchain."""
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from build import BUILD, BT, JAR, JAVA, run


def main():
    output = BUILD / "ui-reader"
    classes = output / "classes"
    classes.mkdir(exist_ok=True, parents=True)
    run([JAVA/"javac.exe", "-encoding", "UTF-8", "--release", "8", "-classpath", JAR, "-d", classes, HERE/"Reader.java"])
    run([BT/"d8.bat", "--lib", JAR, "--min-api", "26", "--output", output, *classes.rglob("*.class")])
    unsigned = output/"unsigned.apk"
    run([BT/"aapt.exe", "package", "-f", "-M", HERE/"AndroidManifest.xml", "-I", JAR, "-F", unsigned])
    with zipfile.ZipFile(unsigned, "a") as z:
        z.write(output/"classes.dex", "classes.dex")
    aligned = output/"aligned.apk"
    run([BT/"zipalign.exe", "-f", "4", unsigned, aligned])
    pkg = output/"course-ui-reader.apk"
    run([BT/"apksigner.bat", "sign", "--ks", BUILD/"local-debug.keystore", "--ks-pass", "pass:android",
         "--key-pass", "pass:android", "--out", pkg, aligned])
    run([BT/"apksigner.bat", "verify", pkg])


if __name__ == "__main__":
    main()
