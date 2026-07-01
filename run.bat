@echo off
REM ============================================================
REM  Portfolio Builder - lanzador local (doble-click)
REM  Levanta el backend (uvicorn) y abre la app en el navegador.
REM ============================================================
cd /d "%~dp0"

if not exist "backend\venv\Scripts\python.exe" (
  echo [!] Falta el entorno virtual. Ejecuta primero setup.bat
  echo.
  pause
  exit /b 1
)

echo Iniciando backend en http://127.0.0.1:8000 ...
start "Portfolio Builder - backend" /d "%~dp0backend" cmd /k "venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Esperando a que el backend responda...
for /l %%i in (1,1,25) do (
  ping -n 2 127.0.0.1 >nul
  curl -s -o nul http://127.0.0.1:8000/ && goto ready
)

echo [!] El backend tardo demasiado en responder. Revisa la ventana del backend.
echo.
pause
exit /b 1

:ready
echo Backend OK. Abriendo la app en el navegador...
start "" "%~dp0frontend\index.html"
echo.
echo Listo. El backend corre en la ventana aparte titulada "Portfolio Builder - backend".
echo Para apagarlo cuando termines, cerra esa ventana (o Ctrl+C ahi).
exit /b 0
