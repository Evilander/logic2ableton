# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for logic2ableton standalone executable

a = Analysis(
    ['logic2ableton/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('logic2ableton/data/DefaultLiveSet.als', 'logic2ableton/data')],
    hiddenimports=[
        'logic2ableton',
        'logic2ableton.cli',
        'logic2ableton.models',
        'logic2ableton.logic_parser',
        'logic2ableton.ableton_generator',
        'logic2ableton.plugin_database',
        'logic2ableton.plugin_matcher',
        'logic2ableton.vst3_scanner',
        'logic2ableton.report',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='logic2ableton',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)
