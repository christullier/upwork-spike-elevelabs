#!/bin/bash
# macOS: double-click to start the app. It opens in your browser; close this window to stop it.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First run: installing uv (the Python tool this app uses)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "Could not install uv. See README, step 1."; read -r -p "Press Enter to close."; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Paste your ElevenLabs API key after ELEVENLABS_API_KEY= and save, then run this again."
  open -e .env
  read -r -p "Press Enter to close."; exit 0
fi
uv run python -m app --open
read -r -p "The app stopped. Press Enter to close."
