@echo off
rem ---------------------------------------------------------------
rem One-click rebuild. Double-click takes about 1 minute.
rem Output: dist\ShangHuoTai\ShangHuoTai.exe
rem
rem WHY chcp 65001: this machine runs with code page 65001 (UTF-8).
rem A .cmd saved as GBK is mis-decoded there, and comments get executed as
rem commands ("not recognized as an internal or external command").
rem So: save this file as UTF-8 (no BOM), CRLF line endings, and set the
rem code page explicitly. Keep comments ASCII-only anyway - defensive.
rem
rem 4.1 main-image rework imports image_lab from ..\taobao-picker at runtime
rem (engine code is NOT copied). --paths lets PyInstaller see it and collect
rem uuid / urllib / PIL deps; missing any makes 4.1 fail with
rem ModuleNotFoundError (uuid was hit for real).
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/3] Stopping running instance...
taskkill /IM 上货台.exe /F >nul 2>&1

rem 2026-10-25: PyInstaller COLLECT deletes the whole dist\上货台 folder,
rem which also wipes the user config (data\settings.json) and the thumbnail
rem cache. Move them aside first, put them back after the build.
if exist "%~dp0dist\上货台\data" (
  if exist "%~dp0_pack_data_bak" rd /s /q "%~dp0_pack_data_bak"
  xcopy /E /I /Y /Q "%~dp0dist\上货台\data" "%~dp0_pack_data_bak" >nul
  echo [1.5/3] data folder saved aside.
)

echo [2/3] Building, please wait about 1 minute...
"F:\pyenvs\douyin\Scripts\pyinstaller.exe" --noconfirm --windowed --name 上货台 --icon "%~dp0app\assets\上货台.ico" --add-data "%~dp0app\assets;app\assets" --paths "%~dp0..\taobao-picker" --hidden-import uuid --hidden-import urllib.request --hidden-import urllib.error --hidden-import urllib.parse --hidden-import http.client --hidden-import email.parser --hidden-import hashlib --hidden-import tempfile --hidden-import secrets --hidden-import mimetypes --hidden-import ssl --hidden-import PIL --hidden-import PIL.Image --hidden-import PIL.ImageDraw --hidden-import PIL.ImageFont --hidden-import server.v3.api --hidden-import server.v3.bundle --hidden-import server.v3.cards --hidden-import server.v3.catmap --hidden-import server.v3.detail_ingest --hidden-import server.v3.detect_slim --hidden-import server.v3.dlcenter --hidden-import server.v3.doudian --hidden-import server.v3.edits --hidden-import server.v3.enrich --hidden-import server.v3.fieldaudit --hidden-import server.v3.finelog --hidden-import server.v3.flow --hidden-import server.v3.grade --hidden-import server.v3.images --hidden-import server.v3.importer --hidden-import server.v3.keywords --hidden-import server.v3.mask --hidden-import server.v3.maskgen --hidden-import server.v3.media --hidden-import server.v3.pack --hidden-import server.v3.pack_merge --hidden-import server.v3.packaging --hidden-import server.v3.packsrc --hidden-import server.v3.positioning --hidden-import server.v3.profile --hidden-import server.v3.purge --hidden-import server.v3.repair --hidden-import server.v3.restore_pack --hidden-import server.v3.safedelete --hidden-import server.v3.sku --hidden-import server.v3.store --hidden-import server.v3.taskcenter --hidden-import server.v3.titles --hidden-import server.v3.trash --hidden-import server.v3.upload --hidden-import server.v3.words --distpath dist --workpath build _frozen_entry.py
if errorlevel 1 goto fail

rem put the user config / thumbnail cache back
if exist "%~dp0_pack_data_bak" (
  if exist "%~dp0dist\上货台\data" rd /s /q "%~dp0dist\上货台\data"
  xcopy /E /I /Y /Q "%~dp0_pack_data_bak" "%~dp0dist\上货台\data" >nul
  rd /s /q "%~dp0_pack_data_bak"
  echo [2.5/3] data folder restored.
)

echo [3/3] Done: dist\上货台\上货台.exe
if /i "%~1"=="/nopause" exit /b 0
pause
exit /b 0

:fail
echo [FAIL] build failed, see messages above.
if /i "%~1"=="/nopause" exit /b 1
pause
exit /b 1
