#!/bin/bash
# Copies the source to CT 104 and the desktop VM and restarts the services.
# The live main model and the ai-toolless setting in brain.json are kept, so changes made on the server survive a deploy.
set -euo pipefail
cd "$(dirname "$0")"
PROXMOX=${PROXMOX:-proxmox}
CT=${CT:-104}
DESKTOP=${DESKTOP:-ai@192.168.1.14}
PUFFER_DIR=/DataStarServ/pufferpanel/servers/servers
ARTY_DISCORD_ID=${ARTY_DISCORD_ID-d124cac6}
ARTY_TELEGRAM_ID=${ARTY_TELEGRAM_ID-7a7e8c6c}
bundle=$(mktemp --suffix=.tgz)
trap 'rm -f "$bundle"' EXIT
tar czf "$bundle" brain/*.py brain/persona.md brain/system-prompt.md brain/brain.json \
  mcp/*.py mcp/mcpo.json bin/ai-pull bin/ai-models bin/ai-use bin/ai-toolless services/*.service \
  apps/beta/ios/index.html apps/beta/ios/app.css apps/beta/ios/sw.js apps/beta/ios/manifest.webmanifest apps/beta/ios/js/*.js \
  apps/beta/ios/fonts/*.ttf apps/beta/ios/icons/*.png
scp -q "$bundle" "$PROXMOX:/tmp/ai-deploy.tgz"
ssh "$PROXMOX" bash -s -- "$CT" <<'REMOTE'
set -euo pipefail
pct push "$1" /tmp/ai-deploy.tgz /root/ai-deploy.tgz
rm -f /tmp/ai-deploy.tgz
pct exec "$1" -- bash -c 'set -e
cd /root && rm -rf deploy && mkdir deploy && tar xzf ai-deploy.tgz -C deploy && rm ai-deploy.tgz
live_model=$(jq -r .model /srv/ai/brain/brain.json 2>/dev/null || true)
live_toolless=$(jq -r ".allow_toolless_models // false" /srv/ai/brain/brain.json 2>/dev/null || echo false)
install -m 644 deploy/brain/* /srv/ai/brain/
if [ -n "$live_model" ] && [ "$live_model" != null ]; then
  jq --arg m "$live_model" --argjson t "$live_toolless" ".model = \$m | .allow_toolless_models = \$t" /srv/ai/brain/brain.json > /srv/ai/brain/brain.json.tmp && mv /srv/ai/brain/brain.json.tmp /srv/ai/brain/brain.json
fi
mkdir -p /srv/ai/brain/state
install -m 644 deploy/mcp/* /srv/ai/mcp/
install -m 755 deploy/bin/* /usr/local/bin/
install -m 644 deploy/services/*.service /etc/systemd/system/
mkdir -p /srv/ai/apps/beta && rm -rf /srv/ai/apps/beta/ios && cp -r deploy/apps/beta/ios /srv/ai/apps/beta/ios && chmod -R u=rwX,go=rX /srv/ai/apps/beta/ios
rm -rf deploy
systemctl daemon-reload
systemctl restart mcpo brain
sleep 10
systemctl is-active mcpo brain'
REMOTE
scp -q arty-pc/browser_agent.py "$DESKTOP:.local/browser-agent/browser_agent.py"
scp -q arty-pc/doc2text "$DESKTOP:/tmp/doc2text"
scp -q arty-pc/run.sh "$DESKTOP:.local/browser-agent/run.sh"
scp -q arty-pc/sign-in-browser "$DESKTOP:.local/bin/sign-in-browser"
scp -q arty-pc/sign-in-browser.desktop "$DESKTOP:.local/share/applications/sign-in-browser.desktop"
scp -q arty-pc/sign-in-google "$DESKTOP:.local/bin/sign-in-google"
scp -q arty-pc/arty-style "$DESKTOP:.local/bin/arty-style"
# Arty's own desktop look (arty-style) uses these dark themes; installs only if missing.
ssh "$DESKTOP" 'dpkg -s arc-theme papirus-icon-theme >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q arc-theme papirus-icon-theme >/dev/null'
ssh "$DESKTOP" '[ -x ~/.local/yt-dlp-venv/bin/pip ] || python3 -m venv ~/.local/yt-dlp-venv
  ~/.local/yt-dlp-venv/bin/pip install -q --upgrade "yt-dlp[default]" && mkdir -p ~/.local/bin && ln -sf ~/.local/yt-dlp-venv/bin/yt-dlp ~/.local/bin/yt-dlp
  dpkg -s python3-openpyxl >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-openpyxl >/dev/null'
scp -q arty-pc/sign-in-google.desktop "$DESKTOP:.local/share/applications/sign-in-google.desktop"
ssh "$DESKTOP" 'mkdir -p ~/.local/browser-agent/gmail-extension'
scp -q extensions/gmail/* "$DESKTOP:.local/browser-agent/gmail-extension/"
ssh "$DESKTOP" 'chmod +x ~/.local/browser-agent/run.sh ~/.local/bin/sign-in-browser ~/.local/bin/sign-in-google ~/.local/bin/arty-style; mkdir -p ~/Desktop
  for launcher in sign-in-browser sign-in-google; do
    install -m 755 ~/.local/share/applications/$launcher.desktop ~/Desktop/$launcher.desktop
    # XFCE only runs desktop launchers whose checksum is recorded as trusted.
    DISPLAY=:0 gio set ~/Desktop/$launcher.desktop metadata::xfce-exe-checksum "$(sha256sum ~/Desktop/$launcher.desktop | cut -d" " -f1)" 2>/dev/null || true
  done'

# The agent's run.sh loop restarts it within seconds; [b] keeps pgrep from matching this command.
ssh "$DESKTOP" 'sudo install -m 755 /tmp/doc2text /usr/local/bin/doc2text && rm /tmp/doc2text; pid=$(pgrep -f "[b]rowser_agent.py" || true); [ -z "$pid" ] || kill $pid'
# The Discord and Telegram bots run on PufferPanel (core-sys); restart them there to pick up changes.
# Each bot is a PufferPanel Docker container (named by its server id) in CT 100; the bot is PID 1 there and
# ignores SIGTERM, so restart the container itself.
for bot in "discord/arty_discord.py:$ARTY_DISCORD_ID" "telegram/arty_telegram.py:$ARTY_TELEGRAM_ID"; do
  source_file=chat-bots/${bot%%:*}
  server_id=${bot##*:}
  if [ -z "$server_id" ]; then
    echo "$(basename "$source_file") skipped: no PufferPanel server id set"
    continue
  fi
  live_file="$PUFFER_DIR/$server_id/$(basename "$source_file")"
  local_sum=$(sha256sum "$source_file" | cut -d' ' -f1)
  live_sum=$(ssh "$PROXMOX" "sha256sum '$live_file' 2>/dev/null | cut -d' ' -f1")
  if [ "$local_sum" = "$live_sum" ]; then
    echo "$(basename "$source_file") unchanged, left running"
    continue
  fi
  scp -q "$source_file" "$PROXMOX:$live_file"
  ssh "$PROXMOX" "chown 100000:100000 '$live_file'"
  if ssh "$PROXMOX" "pct exec 100 -- docker ps -q -f name=^$server_id\$" | grep -q .; then
    ssh "$PROXMOX" "pct exec 100 -- docker restart -t 10 $server_id >/dev/null"
  else
    echo "$(basename "$source_file") updated, but its bot isn't running: start it in PufferPanel"
  fi
done
echo deployed
