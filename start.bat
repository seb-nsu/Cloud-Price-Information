@echo off
REM Startet die Preisverwaltung lokal und oeffnet den Browser - nur Python noetig, keine weiteren Pakete.
REM Danach in der Oberflaeche "Alle Anbieter aktualisieren" klicken. Beenden: dieses Fenster schliessen.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.11 oder neuer fehlt: winget install Python.Python.3.12
  pause
  exit /b 1
)
if not defined PV_HOST set "PV_HOST=127.0.0.1"
start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8080/health | Out-Null; Start-Process http://127.0.0.1:8080; break}catch{Start-Sleep 1}}"
%PY% -m app serve
pause
