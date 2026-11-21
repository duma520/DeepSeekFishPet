@echo off
rem ============================================================
rem  DeepSeek Fish Pet - build with Nuitka (standalone, clang)
rem
rem  IMPORTANT NOTES
rem   1) Keep this file ASCII-only and CRLF encoded, WITHOUT BOM.
rem      cmd locates line boundaries by byte offset; switching
rem      code page in the middle of a UTF-8 bat file shifts the
rem      offsets and the tail of a comment gets executed.
rem   2) --windows-icon-from-ico only sets the icon of the EXE.
rem      The window / tray icon is loaded at runtime by the program
rem      from icon.ico next to the exe, so icon.ico MUST also be
rem      shipped as a data file (see --include-data-files below).
rem      Missing it causes "exe has icon, window has none".
rem   3) The whole build_output\DeepSeekFishPet.dist folder is what
rem      you hand out. Copy .env next to the exe as well.
rem   4) The asset folders are NOT bundled: they are huge and their
rem      names are Chinese, which this file must not contain. See
rem      chapter 8 of the project doc for the exact names. Copy ALL
rem      of these from the project root into the dist folder, next
rem      to the exe, or set asset_root in the settings instead:
rem        a) asset root folder (6 Chinese chars)
rem           = the manifest + the data tables (dao / soul / spirits /
rem             levels / dreams / lines) + the read-only voice packs
rem        b) character image folder (4 Chinese chars)
rem           = the 78 new face images (PNG + same-name GIF) and the
rem             splash image. WITHOUT this folder the pet falls back
rem             to the built-in vector drawing and NO new face shows
rem             up at all -- including the six state faces used when
rem             sleeping / busy / waiting / resting / celebrating /
rem             on error (since v1.1.36 those images come from here).
rem        c) audio folder (4 Chinese chars)
rem           = your own recordings + the voice-line table (JSON)
rem        d) "mahjong" and the number-audio folder (2 Chinese chars)
rem           = the scoring web page and the number clips; missing
rem             them only disables scoring / number reading, no crash
rem   5) ASSET_OPT is an optional hook for extra Nuitka arguments --
rem      leave it unset for a normal build, for example:
rem        set ASSET_OPT=--include-data-files=extra.txt=extra.txt
rem      Moving or replacing assets never needs a change here: this
rem      script only bundles icon.ico, everything else is read from
rem      disk at runtime.
rem   6) The mahjong modules are loaded at RUNTIME with importlib
rem      (from "mahjong\mahjong_api.py" next to the exe), so
rem      --follow-imports never sees what they import. The stdlib
rem      modules below are needed by them but are NOT imported by the
rem      main program, so they must be force-included -- without them
rem      the exe logs "No module named 'http.server'" and the scoring
rem      web page silently never starts. The loose .py files stay the
rem      single source of truth, so mahjong can still be replaced in
rem      the dist folder without rebuilding.
rem      Do NOT put comments inside the multi-line command below:
rem      a "rem" line in the middle of caret continuations breaks it.
rem ============================================================
cd /d "%~dp0"

set "PY=D:\Program Files\Python310\python.exe"
if not exist "%PY%" set "PY=python"

echo Building DeepSeekFishPet.py ...
echo.
echo NOTE: the asset folders are NOT bundled -- their names are Chinese
echo   and this file must stay ASCII-only. After building, copy them
echo   plus .env from the project root into the dist folder next to
echo   the exe. The list is in the comment block at the top of this
echo   file: asset root / character images / audio / mahjong + numbers.
echo.
"%PY%" -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=icon.ico ^
    --include-data-files=icon.ico=icon.ico ^
    --include-module=http.server ^
    --include-module=socketserver ^
    --include-module=urllib.parse ^
    --include-module=argparse ^
    %ASSET_OPT% ^
    --follow-imports ^
    --nofollow-import-to=tkinter,pytest,unittest,IPython,matplotlib,numpy,pandas,scipy,PIL ^
    --jobs=4 ^
    --clang ^
    --remove-output ^
    --output-dir=build_output ^
    DeepSeekFishPet.py

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [OK] Build finished. Dist folder:
    echo      %~dp0build_output\DeepSeekFishPet.dist
    echo      Do not forget to copy .env next to the exe, and to copy
    echo      the asset folders into the dist folder as well: asset
    echo      root / character images / audio / mahjong + numbers.
) else (
    echo [FAILED] Nuitka exit code = %RC%
)
pause
