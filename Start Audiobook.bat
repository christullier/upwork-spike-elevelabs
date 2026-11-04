@echo off
rem Windows: double-click to start the app. It opens in your browser; close this window to stop it.
cd /d "%~dp0"
where uv >nul 2>nul || (
  echo First run: installing uv ^(the Python tool this app uses^)...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Paste your ElevenLabs API key after ELEVENLABS_API_KEY= and save, then run this again.
  notepad .env
  pause
  exit /b
)
uv run python -m app --open
pause
