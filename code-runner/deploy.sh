#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
HOSTS_FILE=${ARTY_HOSTS_FILE:-../hosts.env}
if [ -f "$HOSTS_FILE" ]; then
  . "$HOSTS_FILE"
fi
PROXMOX=${PROXMOX:-proxmox}
RUNNER_CT=${RUNNER_CT:?set RUNNER_CT (the code runner container id), see hosts.env.example}
BRAIN_ADDRESS=${AI_ADDRESS:?set AI_ADDRESS (the brain address the runner accepts), see hosts.env.example}
STAGE=${1:-files}
case "$RUNNER_CT" in
  *[!0-9]*) echo "RUNNER_CT may only hold digits" >&2; exit 1 ;;
esac
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
ssh "$PROXMOX" bash -s -- "$RUNNER_CT" "$remote_bundle" "$STAGE" <<'REMOTE'
set -euo pipefail
pct push "$1" "$2" /root/code-runner.tgz
rm -f "$2"
pct exec "$1" -- env STAGE="$3" bash -c 'set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
rm -rf /root/code-runner-deploy && mkdir /root/code-runner-deploy
tar xzf /root/code-runner.tgz -C /root/code-runner-deploy && rm /root/code-runner.tgz
cd /root/code-runner-deploy
install -d -m 755 /opt/code-runner
install -m 644 runner.py languages.py checks.py parse.ps1 /opt/code-runner/
install -m 755 setup.sh /opt/code-runner/
case "$STAGE" in
  setup) /opt/code-runner/setup.sh all ;;
  firewall)
    nft -c -f nftables.conf
    install -m 644 nftables.conf /etc/nftables.conf
    systemctl enable --quiet nftables
    systemctl restart nftables
    nft list ruleset | grep -c accept ;;
  files) ;;
  *) echo "unknown stage $STAGE: use files, setup or firewall" >&2; exit 1 ;;
esac
install -m 644 code-runner.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet code-runner
systemctl restart code-runner
sleep 2
systemctl is-active code-runner
curl -sSf http://127.0.0.1:8790/health
echo
cd /root && rm -rf /root/code-runner-deploy'
REMOTE
