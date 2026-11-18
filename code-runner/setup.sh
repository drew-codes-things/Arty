#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export DEBIAN_FRONTEND=noninteractive

NODE_VERSION=v26.13.1
TYPESCRIPT_VERSION=5.9.3
NODE_TYPES_VERSION=24.19.2
GO_VERSION=go1.27.4
RUST_VERSION=1.100.0
UV_VERSION=0.13.8
PYTHON_314_VERSION=3.14.10
PYFLAKES_VERSION=4.1.0
KOTLIN_VERSION=2.4.21
POWERSHELL_VERSION=7.7.0
POWERSHELL_SHA256=c89d9c682d2c403dfe726f3df948e359675235a5bd6fab535776de69729ca0cb
DOTNET_CHANNEL=10.0
DOTNET_SDK_VERSION=10.0.404
SANDBOX_USER=sandbox
RUNS=/srv/runs
GO_CACHE=/var/cache/code-runner/go
DEBIAN_PACKAGES=(python3 python3-pyflakes python3-yaml python3-tinycss2 shellcheck gcc g++ libc6-dev
  openjdk-21-jdk-headless php-cli ruby lua5.4 sqlite3 tidy curl ca-certificates xz-utils unzip nftables bc)

download() {
  curl -sSfL --retry 3 -o "$2" "$1"
}

has_version() {
  [ -x "$1" ] && "$@" 2>&1 | grep -qF "$VERSION_WANTED"
}

install_debian_packages() {
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends "${DEBIAN_PACKAGES[@]}" >/dev/null
  apt-get clean
}

install_node() {
  VERSION_WANTED=$NODE_VERSION
  if ! has_version /opt/node/bin/node --version; then
    local file=node-$NODE_VERSION-linux-x64.tar.xz
    download "https://nodejs.org/dist/$NODE_VERSION/$file" "/tmp/$file"
    (cd /tmp && curl -sSf "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" | grep " $file\$" | sha256sum -c -)
    rm -rf /opt/node && mkdir -p /opt/node && tar xJf "/tmp/$file" -C /opt/node --strip-components=1 && rm "/tmp/$file"
  fi
  /opt/node/bin/npm install -g --no-fund --no-audit "typescript@$TYPESCRIPT_VERSION" "@types/node@$NODE_TYPES_VERSION" >/dev/null
  ln -sf /opt/node/bin/node /opt/node/bin/npm /opt/node/bin/npx /opt/node/bin/tsc /usr/local/bin/
}

install_go() {
  VERSION_WANTED=$GO_VERSION
  has_version /opt/go/bin/go version && return
  local file=$GO_VERSION.linux-amd64.tar.gz sum
  sum=$(curl -sSf "https://go.dev/dl/?mode=json&include=all" | python3 -c "import json, sys
print(next(f['sha256'] for r in json.load(sys.stdin) for f in r['files'] if f['filename'] == sys.argv[1]))" "$file")
  download "https://go.dev/dl/$file" "/tmp/$file"
  echo "$sum  /tmp/$file" | sha256sum -c -
  rm -rf /opt/go && tar xzf "/tmp/$file" -C /opt && rm "/tmp/$file"
  ln -sf /opt/go/bin/go /opt/go/bin/gofmt /usr/local/bin/
}

install_rust() {
  VERSION_WANTED=$RUST_VERSION
  has_version /usr/local/bin/rustc --version && return
  local base=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu
  download "$base/rustup-init" /tmp/rustup-init
  echo "$(curl -sSf "$base/rustup-init.sha256" | cut -d' ' -f1)  /tmp/rustup-init" | sha256sum -c -
  chmod +x /tmp/rustup-init
  RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo /tmp/rustup-init -y -q --profile minimal \
    --default-toolchain "$RUST_VERSION" --no-modify-path
  rm /tmp/rustup-init
  for tool in rustc cargo; do
    printf '#!/bin/sh\nexport RUSTUP_HOME=/opt/rustup\nexec /opt/cargo/bin/%s "$@"\n' "$tool" > "/usr/local/bin/$tool"
    chmod 755 "/usr/local/bin/$tool"
  done
}

install_python_314() {
  VERSION_WANTED=$PYTHON_314_VERSION
  if ! has_version /usr/local/bin/python3.14 --version; then
    local file=uv-x86_64-unknown-linux-gnu.tar.gz
    download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$file" "/tmp/$file"
    (cd /tmp && curl -sSfL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$file.sha256" | sha256sum -c -)
    tar xzf "/tmp/$file" -C /tmp
    UV_PYTHON_INSTALL_DIR=/opt/python /tmp/uv-x86_64-unknown-linux-gnu/uv python install "$PYTHON_314_VERSION"
    ln -sf "/opt/python/cpython-$PYTHON_314_VERSION-linux-x86_64-gnu/bin/python3.14" /usr/local/bin/python3.14
    rm -rf "/tmp/$file" /tmp/uv-x86_64-unknown-linux-gnu /root/.local/bin/python3.14
  fi
  /usr/local/bin/python3.14 -m pip install -q --break-system-packages --root-user-action=ignore "pyflakes==$PYFLAKES_VERSION"
}

install_kotlin() {
  VERSION_WANTED=$KOTLIN_VERSION
  has_version /opt/kotlinc/bin/kotlinc -version && return
  local file=kotlin-compiler-$KOTLIN_VERSION.zip
  local base=https://github.com/JetBrains/kotlin/releases/download/v$KOTLIN_VERSION
  download "$base/$file" "/tmp/$file"
  echo "$(curl -sSfL "$base/$file.sha256" | cut -d' ' -f1)  /tmp/$file" | sha256sum -c -
  rm -rf /opt/kotlinc && unzip -q "/tmp/$file" -d /opt && rm "/tmp/$file"
  ln -sf /opt/kotlinc/bin/kotlinc /opt/kotlinc/bin/kotlin /usr/local/bin/
}

install_powershell() {
  VERSION_WANTED=$POWERSHELL_VERSION
  has_version /opt/pwsh/pwsh -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()' && return
  local file=powershell-$POWERSHELL_VERSION-linux-x64.tar.gz
  download "https://github.com/PowerShell/PowerShell/releases/download/v$POWERSHELL_VERSION/$file" "/tmp/$file"
  echo "$POWERSHELL_SHA256  /tmp/$file" | sha256sum -c -
  rm -rf /opt/pwsh && mkdir -p /opt/pwsh && tar xzf "/tmp/$file" -C /opt/pwsh && rm "/tmp/$file"
  chmod 755 /opt/pwsh/pwsh
  ln -sf /opt/pwsh/pwsh /usr/local/bin/pwsh
}

install_dotnet() {
  VERSION_WANTED=$DOTNET_SDK_VERSION
  has_version /opt/dotnet/dotnet --version && return
  local url sum
  read -r url sum < <(curl -sSf "https://builds.dotnet.microsoft.com/dotnet/release-metadata/$DOTNET_CHANNEL/releases.json" \
    | python3 -c "import json, sys
sdk = next(s for r in json.load(sys.stdin)['releases'] for s in r.get('sdks', [r['sdk']]) if s['version'] == sys.argv[1])
found = next(f for f in sdk['files'] if f['rid'] == 'linux-x64' and f['name'].endswith('.tar.gz'))
print(found['url'], found['hash'])" "$DOTNET_SDK_VERSION")
  download "$url" /tmp/dotnet.tgz
  echo "$sum  /tmp/dotnet.tgz" | sha512sum -c -
  rm -rf /opt/dotnet && mkdir -p /opt/dotnet && tar xzf /tmp/dotnet.tgz -C /opt/dotnet && rm /tmp/dotnet.tgz
  ln -sf /opt/dotnet/dotnet /usr/local/bin/dotnet
}

prepare_sandbox() {
  id "$SANDBOX_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SANDBOX_USER"
  mkdir -p "$RUNS"
  grep -q " $RUNS tmpfs " /etc/fstab || echo "tmpfs $RUNS tmpfs size=1536M,mode=0711,nosuid,nodev 0 0" >> /etc/fstab
  systemctl daemon-reload
  findmnt "$RUNS" >/dev/null || mount "$RUNS"
  install -d -o "$SANDBOX_USER" -g "$SANDBOX_USER" -m 755 "$GO_CACHE"
}

warm_go_cache() {
  local folder
  folder=$(mktemp -d)
  printf 'module warm\n\ngo 1.21\n' > "$folder/go.mod"
  printf 'package main\n\nimport (\n%s\n)\n\nfunc main() {}\n' \
    "$(printf '\t_ "%s"\n' bufio bytes context encoding/json errors fmt io math math/rand net/http os os/exec path/filepath \
      regexp slices sort strconv strings sync time unicode maps container/heap encoding/csv crypto/sha256 log)" > "$folder/main.go"
  printf 'package main\n\nimport "testing"\n\nfunc TestWarm(t *testing.T) {}\n' > "$folder/main_test.go"
  chown -R "$SANDBOX_USER:" "$folder"
  runuser -u "$SANDBOX_USER" -- env HOME="$folder" GOCACHE="$GO_CACHE" GOPATH="$folder/.gopath" GOTOOLCHAIN=local GOPROXY=off \
    GOFLAGS=-mod=mod CGO_ENABLED=0 GOTELEMETRY=off sh -c "cd $folder && /usr/local/bin/go vet . && /usr/local/bin/go build -o /dev/null . && /usr/local/bin/go test . >/dev/null"
  rm -rf "$folder"
}

case "${1:-all}" in
  toolchains) install_debian_packages; install_node; install_go; install_rust; install_python_314; install_kotlin
              install_powershell; install_dotnet ;;
  sandbox) prepare_sandbox; warm_go_cache ;;
  all) install_debian_packages; install_node; install_go; install_rust; install_python_314; install_kotlin
       install_powershell; install_dotnet; prepare_sandbox; warm_go_cache ;;
  *) echo "usage: $0 [all|toolchains|sandbox]" >&2; exit 1 ;;
esac
