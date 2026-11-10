# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['_frozen_entry.py'],
    pathex=['G:/Users/Administrator/Documents/deepseek-harness/default-workspace/上货台/../taobao-picker'],
    binaries=[],
    datas=[('G:/Users/Administrator/Documents/deepseek-harness/default-workspace/上货台/app/assets', 'app/assets')],
    hiddenimports=['PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineCore', 'uuid', 'urllib.request', 'urllib.error', 'urllib.parse', 'http.client', 'email.parser', 'hashlib', 'tempfile', 'secrets', 'mimetypes', 'ssl', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'server.v3.api', 'server.v3.bundle', 'server.v3.cards', 'server.v3.catmap', 'server.v3.detail_ingest', 'server.v3.detect_slim', 'server.v3.dlcenter', 'server.v3.doudian', 'server.v3.edits', 'server.v3.enrich', 'server.v3.fieldaudit', 'server.v3.finelog', 'server.v3.flow', 'server.v3.grade', 'server.v3.images', 'server.v3.importer', 'server.v3.keywords', 'server.v3.mask', 'server.v3.maskgen', 'server.v3.media', 'server.v3.pack', 'server.v3.pack_merge', 'server.v3.packaging', 'server.v3.packsrc', 'server.v3.positioning', 'server.v3.profile', 'server.v3.purge', 'server.v3.repair', 'server.v3.restore_pack', 'server.v3.safedelete', 'server.v3.sku', 'server.v3.store', 'server.v3.taskcenter', 'server.v3.titles', 'server.v3.trash', 'server.v3.upload', 'server.v3.words'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='上货台',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['G:/Users/Administrator/Documents/deepseek-harness/default-workspace/上货台/app/assets/上货台.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='上货台',
)

