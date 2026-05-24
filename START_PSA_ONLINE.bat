@echo off
title PSA Queue System Online Launcher
cd /d C:\PSA

start "PSA Flask Server" cmd /k "C:\PSA\.venv\Scripts\python.exe C:\PSA\app.py"

timeout /t 4 >nul

start "PSA Ngrok Tunnel" cmd /k "C:\ngrok\ngrok.exe http 5000"

timeout /t 5 >nul

start "" "http://127.0.0.1:5000/"
start "" "http://127.0.0.1:5000/display"
start "" "http://127.0.0.1:4040"

exit