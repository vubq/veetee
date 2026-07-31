@echo off
rem ES modules can HTTP; mo bang file:// se bi chan.
cd /d "%~dp0"
python -m http.server %1
