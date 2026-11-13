#!/bin/bash
# One-off (and safe to re-run) setup for Arty's extra capabilities. Run from the laptop.
set -euo pipefail
cd "$(dirname "$0")"
HOSTS_FILE=${ARTY_HOSTS_FILE:-./hosts.env}
if [ -f "$HOSTS_FILE" ]; then
  . "$HOSTS_FILE"
fi
PROXMOX=${PROXMOX:-proxmox}
CT=${CT:-104}
DESKTOP_VMID=${DESKTOP_VMID:-105}
AI_ADDRESS=${AI_ADDRESS:?set AI_ADDRESS, see hosts.env.example}
DESKTOP_ADDRESS=${DESKTOP_ADDRESS:?set DESKTOP_ADDRESS, see hosts.env.example}
HOST_ADDRESS=${HOST_ADDRESS:?set HOST_ADDRESS, see hosts.env.example}
NETWORK=${NETWORK:?set NETWORK, see hosts.env.example}
DESKTOP=${DESKTOP:-ai@$DESKTOP_ADDRESS}
HEAL_GUESTS=${HEAL_GUESTS:-}
NEXTCLOUD_CT=${NEXTCLOUD_CT:-}
HOST_FACTS_FILE=${HOST_FACTS_FILE:-}
TIMEZONE=${TIMEZONE:-Europe/London}
SET_BACKUP_JOBS=${SET_BACKUP_JOBS:-yes}
BACKUP_JOB=${BACKUP_JOB:-}
YT_DLP_VERSION=${YT_DLP_VERSION:-}
case "$YT_DLP_VERSION" in
  *[!0-9.]*) echo "YT_DLP_VERSION may only hold digits and dots" >&2; exit 1 ;;
esac
case "$HOST_FACTS_FILE" in
  "" | /*) ;;
  *) echo "HOST_FACTS_FILE must be an absolute path" >&2; exit 1 ;;
esac
case "$HOST_FACTS_FILE" in
  *[!A-Za-z0-9/._-]*) echo "HOST_FACTS_FILE may only hold letters, digits and / . _ -" >&2; exit 1 ;;
esac
case "$BACKUP_JOB" in
  *[!A-Za-z0-9_-]*) echo "BACKUP_JOB may only hold letters, digits, _ and -" >&2; exit 1 ;;
esac

echo "== desktop VM: document, OCR and clipboard tools"
ssh "$DESKTOP" 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq poppler-utils pandoc tesseract-ocr tesseract-ocr-eng xlsx2csv python3-openpyxl xclip wmctrl file ffmpeg >/dev/null && mkdir -p ~/library ~/repos ~/Downloads ~/.local/bin'
ssh "$DESKTOP" "pinned='$YT_DLP_VERSION' sh -s" < arty-pc/install-yt-dlp
echo "   installed"

scp -q bin/homelab-facts "$PROXMOX:/usr/local/sbin/homelab-facts"
scp -q bin/homelab-security "$PROXMOX:/usr/local/sbin/homelab-security"
scp -q bin/arty-gpu-select "$PROXMOX:/usr/local/sbin/arty-gpu-select"
scp -q services/arty-gpu-select.service "$PROXMOX:/etc/systemd/system/arty-gpu-select.service"
scp -q bin/ollama-gpu-env services/ollama-gpu.conf "$PROXMOX:/tmp/"
ssh "$PROXMOX" bash -s -- "$CT" "$DESKTOP_VMID" "$AI_ADDRESS" "$DESKTOP_ADDRESS" "$HOST_ADDRESS" "$NETWORK" \
  "$HEAL_GUESTS" "$NEXTCLOUD_CT" "$TIMEZONE" "$SET_BACKUP_JOBS" "$HOST_FACTS_FILE" "$BACKUP_JOB" <<'REMOTE'
set -euo pipefail
CT=$1
DESKTOP_VMID=$2
AI_ADDRESS=$3
DESKTOP_ADDRESS=$4
HOST_ADDRESS=$5
NETWORK=$6
HEAL_GUESTS=$7
NEXTCLOUD_CT=$8
TIMEZONE=$9
SET_BACKUP_JOBS=${10}
HOST_FACTS_FILE=${11}
BACKUP_JOB=${12}

set_secret() {
  pct exec $CT -- bash -c 'f=/srv/ai/secrets/$1; k=$2; v=$(cat); umask 077; touch "$f"; grep -v "^$k=" "$f" > "$f.tmp" || true; printf "%s=%s\n" "$k" "$v" >> "$f.tmp"; mv "$f.tmp" "$f"; chmod 600 "$f"' _ "$1" "$2"
}
has_secret() {
  pct exec $CT -- grep -q "^$2=" "/srv/ai/secrets/$1" 2>/dev/null
}

echo "== Proxmox: ai@pve can read status everywhere and snapshot VM $DESKTOP_VMID only"
pveum role add ArtyDesktop -privs "VM.Audit VM.Snapshot" 2>/dev/null || pveum role modify ArtyDesktop -privs "VM.Audit VM.Snapshot"
pveum user add ai@pve --comment "Arty AI: read-only, plus snapshots of its own desktop" 2>/dev/null || true
pveum role add ArtyHeal -privs "VM.Audit VM.PowerMgmt" 2>/dev/null || pveum role modify ArtyHeal -privs "VM.Audit VM.PowerMgmt"  # Audit too: a role on a guest replaces the one inherited from /
for vmid in $HEAL_GUESTS; do
  pveum acl modify /vms/$vmid -user ai@pve -role ArtyHeal
  pveum acl modify /vms/$vmid -token 'ai@pve!arty' -role ArtyHeal
done
pveum acl modify / -user ai@pve -role PVEAuditor
pveum acl modify /vms/$DESKTOP_VMID -user ai@pve -role ArtyDesktop
if ! has_secret homelab.env PVE_TOKEN; then
  pveum user token remove ai@pve arty 2>/dev/null || true
  pveum user token add ai@pve arty --privsep 0 --comment "Arty homelab tools" --output-format json \
    | python3 -c 'import json,sys; print("ai@pve!arty=" + json.load(sys.stdin)["value"], end="")' \
    | set_secret homelab.env PVE_TOKEN
fi
pct push $CT /etc/pve/pve-root-ca.pem /srv/ai/secrets/pve-root-ca.pem --perms 644
echo "   done"

echo "== Nextcloud: separate 'arty' account (share folders with it from your own account)"
nc() { pct exec "$NEXTCLOUD_CT" -- docker exec -u www-data "$@"; }
if [ -z "$NEXTCLOUD_CT" ]; then
  echo "   skipped (NEXTCLOUD_CT is empty)"
elif ! nc nextcloud php occ user:info arty >/dev/null 2>&1; then
  # The login password is never needed again (Arty uses an app password), so it is random and discarded.
  openssl rand -base64 33 | pct exec "$NEXTCLOUD_CT" -- bash -c 'read -r pass; docker exec -u www-data -e OC_PASS="$pass" nextcloud php occ user:add --password-from-env --display-name Arty arty >/dev/null'
fi
if [ -n "$NEXTCLOUD_CT" ] && ! has_secret homelab.env NEXTCLOUD_APP_PASSWORD; then
  token=$(nc nextcloud php occ user:auth-tokens:add --name "Arty AI server" arty 2>/dev/null | grep -E '^[A-Za-z0-9-]{20,}$' | tail -1)
  if [ -n "$token" ]; then
    printf '%s' "$token" | set_secret homelab.env NEXTCLOUD_APP_PASSWORD
    printf 'arty' | set_secret homelab.env NEXTCLOUD_USER
  else
    echo "   WARNING: could not create the Nextcloud app password; Nextcloud tools stay off"
  fi
fi
echo "   done"

echo "== CT $CT: 6 GB RAM, time zone $TIMEZONE"
pct set $CT -memory 6144
pct exec $CT -- timedatectl set-timezone "$TIMEZONE"

echo "== CT $CT: SearXNG (private web search)"
pct exec $CT -- bash -s <<'INNER'
set -euo pipefail
mkdir -p /srv/ai/searxng
if [ ! -f /srv/ai/searxng/settings.yml ]; then
  umask 077
  cat > /srv/ai/searxng/settings.yml <<EOF
use_default_settings: true
general:
  instance_name: "Arty search"
server:
  secret_key: "$(openssl rand -hex 32)"
  limiter: false
  image_proxy: false
search:
  safe_search: 0
  formats: [html, json]
EOF
  chmod 644 /srv/ai/searxng/settings.yml
fi
if ! docker inspect searxng >/dev/null 2>&1; then
  docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
    -v /srv/ai/searxng:/etc/searxng -e SEARXNG_BASE_URL=http://127.0.0.1:8888/ searxng/searxng:latest >/dev/null
fi
ollama pull nomic-embed-text >/dev/null
echo "   searxng and the embedding model are up"
INNER

echo "== CT $CT: HTTPS for starfire.ai (browsers only allow the microphone on https)"
pct exec $CT -- bash -s -- "$AI_ADDRESS" <<'INNER'
set -euo pipefail
AI_ADDRESS=$1
command -v caddy >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
cat > /etc/caddy/Caddyfile <<EOF
# starfire.ai (Arty's chat dashboard, on :8090) over HTTPS on the home network, with Caddy's own local CA.
# Trust it on a device by importing /srv/ai/secrets/arty-https-ca.crt (or click through the warning once).
{
	default_sni $AI_ADDRESS
}

https://$AI_ADDRESS:8443, https://$AI_ADDRESS:8444 {
	tls internal
	# Files Arty converts for you: the brain serves them to anyone holding the link's token.
	reverse_proxy /arty-files/* 127.0.0.1:8081
	reverse_proxy 127.0.0.1:8090
}
EOF
systemctl enable --now caddy >/dev/null 2>&1
systemctl reload caddy
for _ in $(seq 1 20); do [ -f /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt ] && break; sleep 1; done
install -m 644 /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt /srv/ai/secrets/arty-https-ca.crt
echo "   https://$AI_ADDRESS:8443 is up"
INNER

echo "== Proxmox: daily host facts (updates waiting, last backups) for Arty's weekly report"
if [ -z "$HOST_FACTS_FILE" ]; then
  echo "   skipped (HOST_FACTS_FILE is empty)"
else
chmod 755 /usr/local/sbin/homelab-facts  # copied over before this block
cat > /etc/systemd/system/homelab-facts.service <<EOF
[Unit]
Description=Write host facts (updates, backups) for Arty's weekly report

[Service]
Type=oneshot
Environment=HOST_FACTS_FILE=$HOST_FACTS_FILE
ExecStart=/usr/local/sbin/homelab-facts
EOF
cat > /etc/systemd/system/homelab-facts.timer <<'EOF'
[Unit]
Description=Daily host facts for Arty

[Timer]
OnCalendar=*-*-* 07:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now homelab-facts.timer >/dev/null
HOST_FACTS_FILE=$HOST_FACTS_FILE /usr/local/sbin/homelab-facts
fi

echo "== Arty API key and alert token"
if ! has_secret brain.env ARTY_API_KEY; then
  printf 'sk-arty-%s' "$(openssl rand -hex 24)" | set_secret brain.env ARTY_API_KEY
fi
# Uptime Kuma's webhook to /hooks/alert carries this token in its URL.
if ! has_secret brain.env ALERT_TOKEN; then
  openssl rand -hex 24 | tr -d "\n" | set_secret brain.env ALERT_TOKEN
fi
pct exec $CT -- bash -c 'umask 077; . /srv/ai/secrets/brain.env
cat > /srv/ai/secrets/arty-api.txt <<EOF
Arty API (home network only). Docs: docs/API.md in the Arty project.
base_url: http://$1:8081/v1
api_key: $ARTY_API_KEY
model: Arty   (or arty/<model> to pick a model per request)

Notifications: Updates in the Arty phone and desktop apps.
EOF' _ "$AI_ADDRESS"
echo "   saved in /srv/ai/secrets/arty-api.txt on CT $CT"

echo "== Proxmox: security watch (new SSH logins, failed-login bursts, new devices, new open ports)"
chmod 755 /usr/local/sbin/homelab-security  # copied over before this block
mkdir -p /etc/homelab
token=$(pct exec $CT -- sed -n 's/^ALERT_TOKEN=//p' /srv/ai/secrets/brain.env)
( umask 077; printf 'BRAIN_URL=http://%s:8081\nALERT_TOKEN=%s\nNETWORK=%s\nHOST_ADDRESS=%s\nEXTRA_SERVERS=%s\n' "$AI_ADDRESS" "$token" "$NETWORK" "$HOST_ADDRESS" "$DESKTOP_ADDRESS" > /etc/homelab/arty.env )
cat > /etc/systemd/system/homelab-security.service <<'EOF'
[Unit]
Description=Homelab security watch (alerts through Arty)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/homelab-security
EOF
cat > /etc/systemd/system/homelab-security.timer <<'EOF'
[Unit]
Description=Homelab security watch every 10 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now homelab-security.timer >/dev/null

echo "== CT $CT: one context size for every Ollama caller"
# A model loaded with one context size is reloaded (and its prompt cache lost) when a caller asks
# for another, so callers that don't say (Open WebUI's titles) get the brain's size too.
pct exec $CT -- bash -c 'mkdir -p /etc/systemd/system/ollama.service.d && printf "[Service]\nEnvironment=\"OLLAMA_CONTEXT_LENGTH=24576\"\nEnvironment=\"OLLAMA_MAX_LOADED_MODELS=2\"\nEnvironment=\"OLLAMA_KV_CACHE_TYPE=q8_0\"\n" > /etc/systemd/system/ollama.service.d/context.conf && systemctl daemon-reload && systemctl restart ollama'

echo "== GPU: CT $CT gets whichever card is fitted (NVIDIA: CUDA, AMD: Vulkan), checked at every boot"
chmod 755 /usr/local/sbin/arty-gpu-select  # copied over before this block
systemctl daemon-reload
systemctl enable arty-gpu-select.service >/dev/null 2>&1
pct exec $CT -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mesa-vulkan-drivers >/dev/null'
pct push $CT /tmp/ollama-gpu-env /usr/local/bin/ollama-gpu-env --perms 755
pct push $CT /tmp/ollama-gpu.conf /etc/systemd/system/ollama.service.d/gpu.conf
rm -f /tmp/ollama-gpu-env /tmp/ollama-gpu.conf
pct exec $CT -- bash -c 'systemctl daemon-reload && systemctl restart ollama'
/usr/local/sbin/arty-gpu-select --dry-run

echo "== CT $CT: media tools (ffmpeg, ImageMagick, faster-whisper speech-to-text)"
pct exec $CT -- bash -s <<'INNER'
set -euo pipefail
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg imagemagick python3-venv >/dev/null
mkdir -p /srv/ai/media/models /srv/ai/brain/files
[ -x /srv/ai/media/venv/bin/python ] || python3 -m venv /srv/ai/media/venv
/srv/ai/media/venv/bin/pip install -q --upgrade "faster-whisper==1.1.1" requests "av<14"  # this faster-whisper imports requests (newer huggingface_hub dropped it) and uses an option PyAV 14 removed
# Fetch the speech model now, so the first voice note doesn't wait for a 500 MB download.
/srv/ai/media/venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='/srv/ai/media/models')"
echo "   ffmpeg $(ffmpeg -version | head -1 | cut -d' ' -f3), $(magick -version | head -1 | cut -d' ' -f2-3), whisper small ready"
INNER

echo "== backups: add the brain and Arty's PC to the vzdump job named in BACKUP_JOB"
if [ "$SET_BACKUP_JOBS" != yes ]; then
  echo "   skipped (SET_BACKUP_JOBS=$SET_BACKUP_JOBS)"
elif [ -z "$BACKUP_JOB" ]; then
  echo "   skipped (BACKUP_JOB is empty)"
else
JOBS_CFG=/etc/pve/jobs.cfg CT=$CT DESKTOP_VMID=$DESKTOP_VMID BACKUP_JOB=$BACKUP_JOB python3 - <<'PY'
import os
import re
import sys
path = os.environ["JOBS_CFG"]
job = os.environ["BACKUP_JOB"]
wanted = [os.environ["CT"], os.environ["DESKTOP_VMID"]]
if not os.path.exists(path):
    print(f"   no {path}, nothing to change")
    sys.exit(0)
text = open(path).read()
found = re.search(rf"^vzdump: {re.escape(job)}\n((?:\t.*\n?)*)", text, re.M)
if not found:
    sys.exit(f"no vzdump job {job} in {path}: set BACKUP_JOB to one of its jobs, or SET_BACKUP_JOBS=no")
vmid = re.search(r"^\tvmid (.+)$", found.group(1), re.M)
if not vmid:
    print(f"   {job} chooses its guests another way (all guests or a pool), left as it is")
    sys.exit(0)
guests = vmid.group(1).split(",")
missing = [guest for guest in wanted if guest not in guests]
if not missing:
    print(f"   {job} already backs up {', '.join(wanted)}")
    sys.exit(0)
start, end = found.start(1) + vmid.start(), found.start(1) + vmid.end()
open(path, "w").write(text[:start] + "\tvmid " + ",".join(guests + missing) + text[end:])
print(f"   added {', '.join(missing)} to {job}")
PY
if [ -f /etc/pve/jobs.cfg ]; then
  grep -E "^\s(vmid|comment|schedule|prune)" /etc/pve/jobs.cfg | sed 's/^/   /'
fi
fi
REMOTE
echo "== provisioned. Now run ./deploy.sh"
