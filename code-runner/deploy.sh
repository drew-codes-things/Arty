#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
HOSTS_FILE=${ARTY_HOSTS_FILE:-../hosts.env}
if [ -f "$HOSTS_FILE" ]; then
  . "$HOSTS_FILE"
fi
PROXMOX=${PROXMOX:-proxmox}
RUNNER_CT=${RUNNER_CT:?set RUNNER_CT (the code runner container id), see hosts.env.example}
BRAIN_CT=${BRAIN_CT:-${CT:-104}}
BRAIN_ADDRESS=${AI_ADDRESS:?set AI_ADDRESS (the brain address the runner accepts), see hosts.env.example}
STAGE=${1:-files}
for container in "$RUNNER_CT" "$BRAIN_CT"; do
  case "$container" in
    ''|*[!0-9]*) echo "RUNNER_CT and BRAIN_CT may only hold digits" >&2; exit 1 ;;
  esac
done
[[ $BRAIN_ADDRESS =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "AI_ADDRESS must be an IPv4 address" >&2; exit 1; }

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp runner.py languages.py checks.py parse.ps1 setup.sh code-runner.service "$staging/"
sed "s/@BRAIN_ADDRESS@/$BRAIN_ADDRESS/" nftables.conf.template > "$staging/nftables.conf"
tar czf "$staging.tgz" -C "$staging" .
remote_bundle=$(ssh "$PROXMOX" mktemp --suffix=.tgz)
[[ $remote_bundle =~ ^/tmp/tmp\.[A-Za-z0-9]+\.tgz$ ]] || { echo "unexpected temporary path on $PROXMOX: $remote_bundle" >&2; exit 1; }
scp -q "$staging.tgz" "$PROXMOX:$remote_bundle"
rm -f "$staging.tgz"
ssh "$PROXMOX" bash -s -- "$RUNNER_CT" "$remote_bundle" "$STAGE" "$BRAIN_CT" <<'REMOTE'
set -euo pipefail
pct push "$1" "$2" /root/code-runner.tgz
rm -f "$2"
brain_needs_restart=$(pct exec "$4" -- bash -c 'set -euo pipefail
secrets=/srv/ai/secrets/brain.env
umask 077
if ! grep -q "^CODE_RUNNER_TOKEN=.\{32,\}" "$secrets"; then
  grep -v "^CODE_RUNNER_TOKEN=" "$secrets" > "$secrets.tmp" || true
  printf "CODE_RUNNER_TOKEN=%s\n" "$(openssl rand -hex 32)" >> "$secrets.tmp"
  mv "$secrets.tmp" "$secrets"
  chmod 600 "$secrets"
fi
brain_pid=$(systemctl show -p MainPID --value brain)
[ "$brain_pid" != 0 ] || exit 0
saved=$(sed -n "s/^CODE_RUNNER_TOKEN=//p" "$secrets")
running=$(tr "\0" "\n" < "/proc/$brain_pid/environ" | sed -n "s/^CODE_RUNNER_TOKEN=//p")
[ "$running" = "$saved" ] || echo yes')
pct exec "$4" -- sed -n "s/^CODE_RUNNER_TOKEN=//p" /srv/ai/secrets/brain.env | pct exec "$1" -- bash -c 'set -euo pipefail
umask 077
install -d -m 700 /etc/code-runner
token=$(head -n 1)
[ ${#token} -ge 32 ] || { echo "the brain has no usable CODE_RUNNER_TOKEN" >&2; exit 1; }
printf "RUNNER_TOKEN=%s\n" "$token" > /etc/code-runner/runner.env.new
mv /etc/code-runner/runner.env.new /etc/code-runner/runner.env'
pct exec "$1" -- env STAGE="$3" bash -c 'set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
rm -rf /root/code-runner-deploy && mkdir /root/code-runner-deploy
tar xzf /root/code-runner.tgz -C /root/code-runner-deploy && rm /root/code-runner.tgz
cd /root/code-runner-deploy
install -d -m 755 /opt/code-runner
install -m 644 runner.py languages.py checks.py parse.ps1 /opt/code-runner/
install -m 755 setup.sh /opt/code-runner/
apply_firewall() {
  nft -c -f nftables.conf
  install -m 644 nftables.conf /etc/nftables.conf
  systemctl enable --quiet nftables
  systemctl restart nftables
  nft list ruleset | grep -c accept
}
case "$STAGE" in
  setup) /opt/code-runner/setup.sh all; apply_firewall ;;
  firewall) apply_firewall ;;
  files) nft list table inet code_runner >/dev/null 2>&1 || apply_firewall ;;
  *) echo "unknown stage $STAGE: use files, setup or firewall" >&2; exit 1 ;;
esac
nft list table inet code_runner >/dev/null || { echo "no code_runner firewall table: not starting the runner" >&2; exit 1; }
[ -s /etc/code-runner/runner.env ] || { echo "no runner token in /etc/code-runner/runner.env: not starting the runner" >&2; exit 1; }
read -r _ systemd_version _ < <(systemctl --version)
[ "${systemd_version%%.*}" -ge 254 ] 2>/dev/null || { echo "systemd 254 or newer is needed for systemd-run --expand-environment, found ${systemd_version:-none}: not starting the runner" >&2; exit 1; }
install -m 644 code-runner.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet code-runner
systemctl restart code-runner
sleep 2
systemctl is-active code-runner
curl -sSf http://127.0.0.1:8790/health
echo
cd /root && rm -rf /root/code-runner-deploy'
if [ "$brain_needs_restart" = yes ]; then
  pct exec "$4" -- systemctl restart brain
  pct exec "$4" -- systemctl is-active brain
fi
REMOTE
