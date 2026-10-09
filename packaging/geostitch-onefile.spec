# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("geostitch") + collect_data_files("pyproj")

a = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=[],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GeoStitch",
    console=False,
    icon="../src/geostitch/assets/app_icon.ico",
)
