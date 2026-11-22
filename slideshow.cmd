@echo off
rem Portable launcher: put this folder on PATH, then run "slideshow" from anywhere.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
%PY% -c "import sys, runpy; sys.path.insert(0, r'%HERE%'); runpy.run_module('slideshow', run_name='__main__')" %*
