@echo off
cd /d "%~dp0"
py canon_scan_pro.py
if errorlevel 1 pause
