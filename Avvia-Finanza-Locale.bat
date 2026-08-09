@echo off
title MyMoney - copia locale (NON e' l'app che usi)
cd /d "%~dp0app"

REM Tutto quello che c'e' qui dentro deve restare in puro ASCII (niente accenti,
REM niente trattini lunghi): con un carattere fuori tabella cmd si disallinea e
REM comincia a eseguire i commenti. Per questo si scrive "e'" e non "e".

echo.
echo  ==========================================================
echo   ATTENZIONE: questa NON e' l'app di tutti i giorni.
echo.
echo   Apre la copia LOCALE, il file su questo computer, fermo
echo   al travaso dell'8 agosto 2026. Quello che scrivi qui
echo   RESTA qui: non arriva sul telefono, non arriva sul cloud.
echo.
echo   Serve solo per guardare i dati vecchi, o per lavorare
echo   sull'app senza internet.
echo.
echo   L'app vera: doppio click su  Avvia-Finanza.bat
echo  ==========================================================
echo.
choice /c SN /n /m "  Aprire lo stesso la copia locale?  [S = si, N = no] "
if errorlevel 2 exit /b

REM Primo avvio: crea l'ambiente isolato e installa le librerie (solo la prima volta).
if not exist ".venv\Scripts\python.exe" (
  echo ============================================================
  echo  PRIMO AVVIO: preparo l'ambiente, ci vuole circa un minuto.
  echo  Succede solo questa volta. Attendi senza chiudere...
  echo ============================================================
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

echo.
echo  Avvio della COPIA LOCALE su http://127.0.0.1:8000
echo  Per CHIUDERE, chiudi questa finestra nera.
echo.
python run.py
pause
