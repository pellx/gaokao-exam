@echo off
setlocal
if defined GCC_BIN (
  set "PATH=%GCC_BIN%;%PATH%"
) else if exist "C:\mingw64\bin\gcc.exe" (
  set "PATH=C:\mingw64\bin;%PATH%"
)
where gcc.exe >nul 2>&1
if errorlevel 1 (
  echo MinGW-w64 GCC missing. Set GCC_BIN to its bin directory. 1>&2
  exit /b 1
)
gcc -std=c11 -O3 -Wall -Wextra -Wpedantic -Werror -static-libgcc -municode "%~dp0controller.c" "%~dp0json.c" "%~dp0platform.c" -o "%~dp0run_2026_all.exe" -lbcrypt
if errorlevel 1 exit /b 1
echo Built %~dp0run_2026_all.exe
