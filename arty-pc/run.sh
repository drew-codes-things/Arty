#!/bin/sh
# Keeps the browser agent running in the desktop session. While ~/.local/browser-agent/paused
# exists (the "Sign in to websites" shortcut is open), it waits instead, so a normal browser
# window can use the same profile.
cd "$HOME"
while true; do
  if [ ! -e "$HOME/.local/browser-agent/paused" ]; then
    "$HOME/.local/browser-agent/venv/bin/python" "$HOME/.local/browser-agent/browser_agent.py" >> "$HOME/.local/browser-agent/agent.log" 2>&1
  fi
  sleep 3
done
