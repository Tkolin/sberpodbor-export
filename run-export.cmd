@echo off
rem Windows launcher: runs the exporter from the script's own folder and appends output to log files.
rem Values below are the ones measured as stable with 3 accounts behind separate proxies.
cd /d %~dp0
set SP_LANE_CONC=14
set SP_INTERVAL_MS=100
set SP_REQ_TIMEOUT_MS=30000
node export.js >> run.log 2>> run.err.log
