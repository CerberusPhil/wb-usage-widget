# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
SCRIPTS = ROOT / 'scripts'
ICON = ROOT / 'build' / 'macos' / 'widget_icon.icns'

datas = [
    (str(SCRIPTS / 'billing_widget_template.html'), '.'),
    (str(SCRIPTS / 'billing_template.html'), '.'),
    (str(SCRIPTS / 'widget_icon.ico'), '.'),
    (str(SCRIPTS / 'cookie_auto_renew.py'), '.'),
    (str(SCRIPTS / 'save_cookie.py'), '.'),
    (str(ROOT / 'icons'), 'icons'),
    (str(ICON), '.'),
]
# The Cocoa backend loads pywebview's JavaScript bridge at runtime.
datas += collect_data_files('webview', subdir='js')
datas += copy_metadata('pywebview')
# Keep HTTPS verification portable across machines with different Python installs.
datas += collect_data_files('certifi')

a = Analysis(
    [str(SCRIPTS / 'wb_widget_app.py')],
    pathex=[str(SCRIPTS)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'webview.platforms.cocoa',
        'objc',
        'Foundation',
        'AppKit',
        'WebKit',
        'Quartz',
        'PyObjCTools.AppHelper',
        'cookie_auto_renew',
        'save_cookie',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / 'packaging' / 'macos_certificates.py')],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WBCreditWidget',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='WBCreditWidget',
)
app = BUNDLE(
    collection,
    name='WBCreditWidget.app',
    icon=str(ICON),
    bundle_identifier='com.workbuddy.WBCreditWidget',
    info_plist={
        'CFBundleDisplayName': 'Workbuddy积分看板',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    },
)
