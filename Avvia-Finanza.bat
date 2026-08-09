@echo off
title MyMoney

REM ===========================================================================
REM  MyMoney sta sul cloud. Telefono e PC aprono la STESSA app sullo STESSO
REM  database: non c'e' niente da sincronizzare, e niente che possa divergere.
REM
REM  Prima questo file avviava un server sul computer, che leggeva il file
REM  app\data\finanza.db. Dal travaso (8 agosto 2026) quel file e' una COPIA
REM  ferma: continuare ad aprirlo voleva dire scrivere movimenti che il
REM  telefono non avrebbe mai visto. Per aprirla lo stesso (e' ancora il backup
REM  piu' completo che c'e' su questa macchina) usa Avvia-Finanza-Locale.bat,
REM  che lo dice chiaro prima di partire.
REM
REM  L'indirizzo e' scritto anche in docs\PIANO-CLOUD.md e nella console di
REM  Google (URI di reindirizzo OAuth): se cambia, cambialo in tutti e tre.
REM
REM  ATTENZIONE, questo file deve restare in puro ASCII: niente accenti, niente
REM  trattini lunghi. Un carattere fuori tabella e cmd perde il conto fra byte
REM  e caratteri, poi esegue pezzi di questi commenti come se fossero comandi.
REM  E' il motivo per cui qui non c'e' nessun "chcp".
REM ===========================================================================
set "MYMONEY_URL=https://mymoney-1057159819758.europe-west8.run.app"

start "" "%MYMONEY_URL%"
