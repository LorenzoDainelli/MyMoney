@echo off
title MyMoney - pubblica sul cloud
REM ---------------------------------------------------------------------------
REM Manda la versione che sta in QUESTA cartella sul servizio online.
REM
REM ATTENZIONE, questo file deve restare in puro ASCII: niente accenti, niente
REM trattini lunghi. Un carattere fuori tabella e cmd perde il conto fra byte
REM e caratteri, poi esegue pezzi di questi commenti come se fossero comandi.
REM E' il motivo per cui qui non c'e' nessun "chcp".
REM
REM PERCHE' ESISTE QUESTO FILE. Il comando da riga di comando e'
REM    gcloud run deploy mymoney --source . --region europe-west8
REM e quel "." vuol dire "la cartella in cui sei adesso". Lanciato per sbaglio
REM da C:\Users\loren, il 13/08/2026, ha provato a impacchettare la cartella
REM utente INTERA: dentro ci sono Desktop\Claude\tools (password di Cloud SQL,
REM token, credenziali OAuth) e app\data\finanza.db con i movimenti veri. Il
REM .gcloudignore che li tiene fuori sta DENTRO il repo, quindi da li' non
REM proteggeva niente. Si e' fermato da solo su una cartella del telefono
REM collegato, prima di caricare: e' andata bene per caso, non per difesa.
REM
REM Qui il "." non c'e'. %~dp0 e' la cartella di QUESTO file, cioe' il repo,
REM sempre, da qualunque punto lo si lanci. Non si puo' sbagliare bersaglio.
REM ---------------------------------------------------------------------------

set "GCLOUD=C:\Users\loren\Desktop\Claude\tools\google-cloud-sdk\bin\gcloud.cmd"
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

if not exist "%GCLOUD%" (
  echo Non trovo gcloud in:
  echo   %GCLOUD%
  echo.
  pause
  exit /b 1
)

if not exist "%REPO%\.gcloudignore" (
  echo ATTENZIONE: in questa cartella non c'e' il .gcloudignore.
  echo E' il file che tiene fuori dal pacchetto il database e le chiavi.
  echo Senza, non si pubblica.
  echo.
  pause
  exit /b 1
)

echo.
echo Cartella da pubblicare:
echo   %REPO%
echo.
echo Controllo cosa finirebbe nel pacchetto...
echo.

REM Prima si guarda, poi si spedisce. Se in questo elenco comparisse un .db o
REM un file di chiavi, e' il momento di fermarsi.
REM `call` NON e' decorativo: gcloud.cmd e' a sua volta un file batch, e un .bat
REM chiamato da un .bat senza `call` PRENDE IL POSTO di chi lo chiama e non
REM torna piu' indietro. Senza, qui lo script moriva in silenzio subito dopo
REM aver scritto l'elenco: nessun errore, semplicemente non esisteva piu'.
call "%GCLOUD%" meta list-files-for-upload "%REPO%" > "%TEMP%\mymoney-elenco.txt" 2>&1
if errorlevel 1 (
  echo Non sono riuscito a leggere l'elenco dei file.
  type "%TEMP%\mymoney-elenco.txt"
  echo.
  pause
  exit /b 1
)

for /f %%N in ('find /c /v "" ^< "%TEMP%\mymoney-elenco.txt"') do set "QUANTI=%%N"
echo   file da spedire: %QUANTI%

set "SOSPETTI=0"
findstr /i /r "app\\data \.db$ \.env oauth\.txt pgpw cloudsql-pw job-token service-account" "%TEMP%\mymoney-elenco.txt" > "%TEMP%\mymoney-sospetti.txt"
if not errorlevel 1 set "SOSPETTI=1"

if "%SOSPETTI%"=="1" (
  echo.
  echo ==========================================================
  echo   FERMO TUTTO. Nel pacchetto ci sono dati che non devono
  echo   uscire da questo PC:
  echo ==========================================================
  type "%TEMP%\mymoney-sospetti.txt"
  echo.
  echo Non pubblico. Controlla il .gcloudignore.
  echo.
  pause
  exit /b 1
)

echo   segreti o database: nessuno
echo.
choice /c SN /n /m "Pubblico sul cloud? [S/N] "
if errorlevel 2 (
  echo Annullato. Non e' cambiato niente.
  echo.
  pause
  exit /b 0
)

echo.
echo Pubblico. Ci vogliono qualche minuto.
echo.
call "%GCLOUD%" run deploy mymoney --source "%REPO%" --region europe-west8

echo.
if errorlevel 1 (
  echo La pubblicazione NON e' riuscita. Il servizio online e' rimasto
  echo com'era: nessun danno, si puo' riprovare.
) else (
  echo Fatto. Apri l'app con Avvia-Finanza.bat per vedere la versione nuova.
)
echo.
pause
