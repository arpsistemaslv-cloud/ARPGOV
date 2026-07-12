@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado. Criando .venv...
    py -3 -m venv .venv 2>nul
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 (
        echo ERRO: nao foi possivel criar o venv. Instale Python 3 de python.org e marque "Add to PATH".
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip -q
    pip install -r requirements.txt
    echo.
)

echo Verificando dependencias...
.\.venv\Scripts\pip install -r requirements.txt -q
echo.
echo Portal: http://127.0.0.1:5001/
echo Se o navegador nao abrir sozinho, copie o link acima ^(use 127.0.0.1, nao "localhost"^).
echo.
.\.venv\Scripts\python.exe app.py
if errorlevel 1 (
    echo.
    echo Se deu erro de porta em uso, feche outro terminal com o portal ou defina PORTAL_PORT=5002
    echo.
)
pause
