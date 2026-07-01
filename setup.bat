@echo off
REM ============================================================
REM  Portfolio Builder - setup de primera vez (doble-click)
REM  Crea el entorno virtual e instala las dependencias.
REM  Requiere Python 3.12+ instalado desde python.org
REM  (NO el "python" del Microsoft Store, que es un stub).
REM ============================================================
cd /d "%~dp0backend"

echo Buscando Python...
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

echo Creando entorno virtual con %PY% ...
%PY% -m venv venv
if not exist "venv\Scripts\python.exe" (
  echo.
  echo [!] No se pudo crear el venv. Instala Python 3.12+ desde https://python.org
  echo     Si "python" abre la Microsoft Store, desactiva el alias en
  echo     Configuracion ^> Aplicaciones ^> Alias de ejecucion de aplicaciones.
  pause
  exit /b 1
)

echo Instalando dependencias (puede tardar un rato)...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo [i] Cree backend\.env desde el ejemplo.
  echo     ABRILO con el bloc de notas y pega tu FMP_API_KEY antes de usar la app.
)

echo.
echo ============================================================
echo  Setup completo. Ahora usa run.bat para levantar la app.
echo ============================================================
pause
