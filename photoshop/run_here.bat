@echo off
setlocal
REM ============================================================
REM   Double-click to process EVERY .png in THIS folder:
REM   scale each layer to a 3600px longest edge, center it on
REM   the 4000x4000 canvas, and overwrite the PNG in place.
REM   Keep run_folder.ps1 + process_bottle.jsx next to this file.
REM ============================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_folder.ps1"
echo.
echo ============================================================
echo  Finished. Review the results above (errors, if any, listed).
echo ============================================================
pause
