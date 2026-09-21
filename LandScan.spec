# PyInstaller spec — builds a single-file app on Windows and macOS.
# Run: pyinstaller LandScan.spec
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
datas = collect_data_files("customtkinter") + collect_data_files("rasterio") + collect_data_files("pyproj") + [("assets", "assets")]
hiddenimports = collect_submodules("rasterio") + collect_submodules("pyproj") + ["laspy", "lazrs", "PIL._tkinter_finder"]

a = Analysis(["main.py"], pathex=["."], binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
             excludes=["matplotlib", "pandas", "IPython", "jupyter", "notebook", "pytest"], cipher=block_cipher, noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
icon = "assets/icon.ico" if sys.platform == "win32" else ("assets/icon.icns" if sys.platform == "darwin" else None)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [], name="Common Unity LandScan", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, icon=icon)
if sys.platform == "darwin":
    app = BUNDLE(exe, name="Common Unity LandScan.app", icon="assets/icon.icns", bundle_identifier="com.commonunity.landscan",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "1.0.0"})
