"""Build the executable.

A plain `pyinstaller packaging/app.py` produces 336 MB, because PyInstaller
follows what is importable rather than what is imported: torch, OpenCV,
transformers, pandas and scipy all live in the same site-packages and all come
along. Only one of them is even reachable from this application - qfluentwidgets
imports scipy for an acrylic blur - and that import is already guarded by a
try/except with a working fallback, so excluding it costs an effect this
application never uses.

The result is 51 MB, and the build takes half a minute rather than five.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Present in the environment, never used here.
UNUSED = """
    torch torchvision torchaudio cv2 transformers tokenizers huggingface_hub
    safetensors hf_xet scipy numpy PIL colorthief pandas matplotlib sympy
    networkx grpc google protobuf pyarrow sklearn IPython notebook jupyter
    pytest setuptools pycountry mcp anthropic openai httpx cryptography
    pydantic pydantic_core yaml regex tqdm requests urllib3
    charset_normalizer win32com pythonwin PyInstaller tkinter
""".split()

# Qt is modular and only a few modules are used.
UNUSED_QT = """
    QtWebEngineCore QtWebEngineWidgets QtWebEngineQuick QtQuick QtQuick3D
    QtQuickWidgets QtQml Qt3DCore Qt3DRender QtCharts QtDataVisualization
    QtMultimedia QtMultimediaWidgets QtPdf QtDesigner QtUiTools QtTest QtSql
    QtBluetooth QtNfc QtPositioning QtSensors QtSerialPort QtWebSockets
    QtWebChannel QtRemoteObjects QtScxml QtStateMachine QtHelp QtNetworkAuth
    QtOpenGL QtOpenGLWidgets QtTextToSpeech QtDBus QtConcurrent QtSpatialAudio
""".split()


def main():
    icon = os.path.join(HERE, "icon.ico")
    if not os.path.exists(icon):
        print("no icon yet; run packaging/make_icon.py first")
        return 1

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
           "--windowed", "--name", "SmartEscTool",
           "--icon", icon,
           "--manifest", os.path.join(HERE, "app.manifest"),
           "--add-data", "%s;." % icon,
           "--add-data", "%s;." % os.path.join(HERE, "icon.png"),
           "--distpath", os.path.join(ROOT, "dist"),
           "--workpath", os.path.join(ROOT, "build", "work"),
           "--specpath", os.path.join(ROOT, "build"),
           "--collect-all", "qfluentwidgets",
           "--hidden-import", "serial.tools.list_ports"]
    for name in UNUSED:
        cmd += ["--exclude-module", name]
    for name in UNUSED_QT:
        cmd += ["--exclude-module", "PySide6." + name]
    cmd.append(os.path.join(HERE, "app.py"))

    print("building...")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        return result.returncode
    out = os.path.join(ROOT, "dist", "SmartEscTool.exe")
    print("%s, %.0f MB" % (out, os.path.getsize(out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
