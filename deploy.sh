#!/bin/bash
# Copies the source to CT 104 and the desktop VM and restarts the services.
# The live main model and the ai-toolless setting in brain.json are kept, so changes made on the server survive a deploy.
set -euo pipefail
cd "$(dirname "$0")"
HOSTS_FILE=${ARTY_HOSTS_FILE:-./hosts.env}
if [ -f "$HOSTS_FILE" ]; then
  . "$HOSTS_FILE"
fi
PROXMOX=${PROXMOX:-proxmox}
CT=${CT:-104}
DESKTOP=${DESKTOP:-ai@${DESKTOP_ADDRESS:?set DESKTOP_ADDRESS, see hosts.env.example}}
PUFFER_DIR=/DataStarServ/pufferpanel/servers/servers
PUFFER_CT=${PUFFER_CT:-100}
PUFFER_UID=${PUFFER_UID:-100000}
YT_DLP_VERSION=${YT_DLP_VERSION:-}
case "$PUFFER_CT$PUFFER_UID$YT_DLP_VERSION" in
  *[!0-9.]*) echo "PUFFER_CT, PUFFER_UID and YT_DLP_VERSION may only hold digits and dots" >&2; exit 1 ;;
esac
PINNED_DESKTOP_KEY=desktop_known_hosts
[ -s "$PINNED_DESKTOP_KEY" ] || { echo "no pinned desktop host key: run bin/pin-desktop-host-key first" >&2; exit 1; }
ARTY_DISCORD_ID=${ARTY_DISCORD_ID-d124cac6}
ARTY_TELEGRAM_ID=${ARTY_TELEGRAM_ID-7a7e8c6c}
bundle=$(mktemp --suffix=.tgz)
trap 'rm -f "$bundle"' EXIT
tar czf "$bundle" --exclude=services/arty-desktop-egress.service --exclude='mcp/test_*.py' brain/*.py brain/persona.md brain/system-prompt.md brain/brain.json \
  mcp/*.py mcp/mcpo.json "$PINNED_DESKTOP_KEY" bin/ai-pull bin/ai-models bin/ai-use bin/ai-toolless bin/mcpo-start services/*.service \
  apps/ios/index.html apps/ios/app.css apps/ios/sw.js apps/ios/manifest.webmanifest apps/ios/js/*.js apps/ios/js/vendor/*.js \
  apps/ios/fonts/*.ttf apps/ios/icons/*.png
remote_bundle=$(ssh "$PROXMOX" mktemp --suffix=.tgz)
[[ $remote_bundle =~ ^/tmp/tmp\.[A-Za-z0-9]+\.tgz$ ]] || { echo "unexpected temporary path on $PROXMOX: $remote_bundle" >&2; exit 1; }
scp -q "$bundle" "$PROXMOX:$remote_bundle"
ssh "$PROXMOX" bash -s -- "$CT" "$remote_bundle" <<'REMOTE'
set -euo pipefail
pct push "$1" "$2" /root/ai-deploy.tgz
rm -f "$2"
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
install -m 644 deploy/desktop_known_hosts /srv/ai/mcp/workspace_known_hosts
install -m 755 deploy/bin/* /usr/local/bin/
install -m 644 deploy/services/*.service /etc/systemd/system/
mkdir -p /srv/ai/apps
rm -rf /srv/ai/apps/ios.new
cp -r deploy/apps/ios /srv/ai/apps/ios.new
chmod -R u=rwX,go=rX /srv/ai/apps/ios.new
rm -rf /srv/ai/apps/ios.old
[ ! -d /srv/ai/apps/ios ] || mv /srv/ai/apps/ios /srv/ai/apps/ios.old
mv /srv/ai/apps/ios.new /srv/ai/apps/ios || { [ ! -d /srv/ai/apps/ios.old ] || mv /srv/ai/apps/ios.old /srv/ai/apps/ios; exit 1; }
rm -rf deploy
systemctl daemon-reload
systemctl restart mcpo brain
sleep 10
systemctl is-active mcpo brain
rm -rf /srv/ai/apps/ios.old
[ ! -d /srv/ai/apps/beta/ios ] || echo "the old copy at /srv/ai/apps/beta/ios was left in place: delete it once the new one is checked"'
REMOTE
scp -q arty-pc/browser_agent.py "$DESKTOP:.local/browser-agent/browser_agent.py"
doc2text_upload=$(ssh "$DESKTOP" mktemp)
scp -q arty-pc/doc2text "$DESKTOP:$doc2text_upload"
scp -q arty-pc/run.sh "$DESKTOP:.local/browser-agent/run.sh"
scp -q arty-pc/sign-in-browser "$DESKTOP:.local/bin/sign-in-browser"
scp -q arty-pc/sign-in-browser.desktop "$DESKTOP:.local/share/applications/sign-in-browser.desktop"
scp -q arty-pc/sign-in-google "$DESKTOP:.local/bin/sign-in-google"
scp -q arty-pc/arty-style "$DESKTOP:.local/bin/arty-style"
# Arty's own desktop look (arty-style) uses these dark themes; installs only if missing.
ssh "$DESKTOP" 'dpkg -s arc-theme papirus-icon-theme >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q arc-theme papirus-icon-theme >/dev/null'
ssh "$DESKTOP" "pinned='$YT_DLP_VERSION' sh -s" < arty-pc/install-yt-dlp
ssh "$DESKTOP" 'dpkg -s python3-openpyxl >/dev/null 2>&1 || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-openpyxl >/dev/null'
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
ssh "$DESKTOP" "sudo install -m 755 '$doc2text_upload' /usr/local/bin/doc2text && rm -f '$doc2text_upload'"'; pid=$(pgrep -f "[b]rowser_agent.py" || true); [ -z "$pid" ] || kill $pid'
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
  ssh "$PROXMOX" "chown $PUFFER_UID:$PUFFER_UID '$live_file'"
  if ssh "$PROXMOX" "pct exec $PUFFER_CT -- docker ps -q -f name=^$server_id\$" | grep -q .; then
    ssh "$PROXMOX" "pct exec $PUFFER_CT -- docker restart -t 10 $server_id >/dev/null"
  else
    echo "$(basename "$source_file") updated, but its bot isn't running: start it in PufferPanel"
  fi
done
echo deployed
