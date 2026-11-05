"""The homelab: Proxmox status, desktop snapshots, Uptime Kuma and Nextcloud.

Proxmox access is an API token for `ai@pve` that can read status everywhere and
snapshot only VM 105 (the assistant's desktop); it can't start, stop, change or
delete anything else. Nextcloud access is a separate `arty` account, so it only
sees what Drew shares with it. PufferPanel access is an OAuth client limited to
viewing, starting and stopping game servers. Each part switches itself off when its secret is missing.
"""
import base64
import json
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree

from mcp.server.fastmcp import FastMCP

from remote import clip, load_env_file, quoted, ssh
from remote import text as remote_text

load_env_file(os.environ.get("HOMELAB_ENV", "/srv/ai/secrets/homelab.env"))

PVE_URL = os.environ.get("PVE_URL", "https://192.168.1.253:8006")
PVE_NODE = os.environ.get("PVE_NODE", "servmox")
PVE_TOKEN = os.environ.get("PVE_TOKEN", "")
PVE_CA = os.environ.get("PVE_CA", "/srv/ai/secrets/pve-root-ca.pem")
DESKTOP_VMID = int(os.environ.get("DESKTOP_VMID", "105"))
SNAPSHOT_PREFIX = "arty_"
SNAPSHOTS_KEPT = int(os.environ.get("DESKTOP_SNAPSHOTS_KEPT", "3"))
KUMA_URL = os.environ.get("UPTIME_KUMA_URL", "http://192.168.1.11:3001")
KUMA_KEY = os.environ.get("UPTIME_KUMA_API_KEY", "")
NC_URL = os.environ.get("NEXTCLOUD_URL", "http://192.168.1.10:8080").rstrip("/")
NC_USER = os.environ.get("NEXTCLOUD_USER", "")
NC_PASSWORD = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
PUFFER_URL = os.environ.get("PUFFERPANEL_URL", "http://192.168.1.10:8090").rstrip("/")
PUFFER_CLIENT_ID = os.environ.get("PUFFERPANEL_CLIENT_ID", "")
PUFFER_CLIENT_SECRET = os.environ.get("PUFFERPANEL_CLIENT_SECRET", "")
PUFFER_ACTIONS = ("start", "stop", "restart")
TOKEN_REFRESH_MARGIN_SECONDS = 60
NC_UPLOAD_FOLDER = "Arty"
NC_LOCAL_DIR = "~/nextcloud"
KUMA_STATES = {"0": "DOWN", "1": "up", "2": "pending", "3": "maintenance"}
DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
GIB = 1024 ** 3

mcp = FastMCP("homelab")
_puffer_token = {"value": "", "expires": 0.0}


def _pve(method: str, path: str, params: dict | None = None):
    if not PVE_TOKEN:
        raise RuntimeError("Proxmox access isn't set up (PVE_TOKEN missing).")
    data = urllib.parse.urlencode(params).encode() if params and method != "GET" else None
    url = f"{PVE_URL}/api2/json{path}" + (f"?{urllib.parse.urlencode(params)}" if params and method == "GET" else "")
    request = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"PVEAPIToken={PVE_TOKEN}"})
    context = ssl.create_default_context(cafile=PVE_CA)
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return json.loads(response.read())["data"]
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Proxmox said {error.code}: {error.read().decode(errors='replace')[:200]}") from None


def _gib(value) -> str:
    return f"{(value or 0) / GIB:.1f}G"


GUEST_ADDRESS = re.compile(r"(?:^|,)ip=(\d+\.\d+\.\d+\.\d+)")


def _guest_address(item: dict) -> str:
    """A guest's fixed IP address from its Proxmox config (containers: net0; cloud-init VMs: ipconfig0).
    Empty when it isn't set there (DHCP, or set inside the guest itself)."""
    kind = "lxc" if item["type"] == "lxc" else "qemu"
    try:
        config = _pve("GET", f"/nodes/{item['node']}/{kind}/{item['vmid']}/config")
    except RuntimeError:
        return ""
    match = GUEST_ADDRESS.search(str(config.get("net0" if kind == "lxc" else "ipconfig0", "")))
    return match.group(1) if match else ""


def _host_address(node: str) -> str:
    try:
        interfaces = _pve("GET", f"/nodes/{node}/network")
    except RuntimeError:
        return ""
    return next((i["address"] for i in interfaces if i.get("iface") == "vmbr0" and i.get("address")), "")


@mcp.tool()
def proxmox_status() -> str:
    """Status of the home server (Proxmox): host CPU/RAM, every VM and container (its IP address, running or not,
    CPU, RAM, uptime) and storage space. Use it for "what are the IPs". Read-only."""
    try:
        resources = _pve("GET", "/cluster/resources")
    except RuntimeError as error:
        return str(error)
    lines = []
    for item in resources:
        if item.get("type") == "node":
            address = _host_address(item["node"])
            lines.append(f"Host {item['node']}{f' ({address})' if address else ''}: {item.get('status')}, CPU {item.get('cpu', 0) * 100:.0f}% of {item.get('maxcpu')} cores, "
                         f"RAM {_gib(item.get('mem'))}/{_gib(item.get('maxmem'))}, up {int(item.get('uptime', 0)) // 3600}h")
    guests = sorted((i for i in resources if i.get("type") in ("qemu", "lxc")), key=lambda i: i["vmid"])
    for item in guests:
        kind = "VM" if item["type"] == "qemu" else "CT"
        if item.get("status") == "running":
            detail = f"running, CPU {item.get('cpu', 0) * 100:.0f}%, RAM {_gib(item.get('mem'))}/{_gib(item.get('maxmem'))}, up {int(item.get('uptime', 0)) // 3600}h"
        else:
            detail = item.get("status", "unknown")
        address = _guest_address(item)
        lines.append(f"{kind} {item['vmid']} {item.get('name', '')}{f' ({address})' if address else ''}: {detail}")
    for item in resources:
        if item.get("type") == "storage" and item.get("maxdisk"):
            lines.append(f"Storage {item['storage']}: {_gib(item.get('disk'))} used of {_gib(item.get('maxdisk'))}")
    return "\n".join(lines)


STORAGE_WARN_FRACTION = 0.85
BACKUP_STALE_DAYS = 8
# Proxmox only lists updates to accounts with Sys.Modify, and backup files to accounts that may make
# backups; Arty's read-only account has neither, so a host timer (homelab-facts, see provision.sh)
# writes both here every morning.
HOST_FACTS_FILE = os.environ.get("HOST_FACTS_FILE", "/srv/ai/state/host-facts.json")


@mcp.tool()
def weekly_report() -> str:
    """The homelab's weekly health report: storage space (warning over 85%), when each VM/container was
    last backed up (warning if not in the last 8 days, or if a backup job failed), and system updates
    waiting on the Proxmox host. Read-only."""
    lines, warnings = [], []
    try:
        resources = _pve("GET", "/cluster/resources")
    except RuntimeError as error:
        return str(error)
    for item in resources:
        if item.get("type") == "storage" and item.get("maxdisk"):
            used = (item.get("disk") or 0) / item["maxdisk"]
            lines.append(f"Storage {item['storage']}: {used * 100:.0f}% full ({_gib(item.get('disk'))} of {_gib(item.get('maxdisk'))})")
            if used >= STORAGE_WARN_FRACTION:
                warnings.append(f"storage {item['storage']} is {used * 100:.0f}% full")
    guests = {i["vmid"]: i.get("name", "") for i in resources if i.get("type") in ("qemu", "lxc")}
    try:
        with open(HOST_FACTS_FILE) as handle:
            facts = json.load(handle)
    except (OSError, ValueError):
        facts = {}
        warnings.append("the host facts file is missing, so backups and updates weren't checked (run homelab-facts on the host)")
    # When each guest was last backed up, from the backup files (a job backing up several guests is one
    # task with no guest number, so the task list can't say).
    last_backup = {int(vmid): stamp for vmid, stamp in (facts.get("backups") or {}).items() if str(vmid).isdigit()}
    for vmid, name in sorted(guests.items()):
        stamp = last_backup.get(vmid)
        when = time.strftime("%a %d %b", time.localtime(stamp)) if stamp else "never (not in a backup job)"
        lines.append(f"Backup of {vmid} {name}: {when}")
        if stamp and time.time() - stamp > BACKUP_STALE_DAYS * 86400:
            warnings.append(f"{vmid} {name} hasn't been backed up since {when}")
    try:
        tasks = _pve("GET", f"/nodes/{PVE_NODE}/tasks", {"typefilter": "vzdump", "limit": 50, "source": "all"})
        for task in tasks:
            if task.get("status") not in ("OK", None) and time.time() - task.get("starttime", 0) < 7 * 86400:
                warnings.append(f"a backup job failed on {time.strftime('%a %d %b', time.localtime(task['starttime']))}: {task.get('status')}")
    except RuntimeError as error:
        warnings.append(f"couldn't read the backup job history ({error})")
    updates = facts.get("updates")
    if updates:
        checked = time.strftime("%a %d %b", time.localtime(facts.get("time", 0)))
        lines.append(f"Updates waiting on the host: {updates.get('count', 0)} ({updates.get('security', 0)} security), checked {checked}")
        if updates.get("security"):
            warnings.append(f"{updates['security']} security updates are waiting on the Proxmox host")
    head = ("WARNINGS: " + "; ".join(dict.fromkeys(warnings)) + ".") if warnings else "No warnings: everything looks healthy."
    return head + "\n" + "\n".join(lines)


# ---------- self-healing ----------
# Arty may start or restart these guests (and only these: never its own container), and only after the user
# approves (homelab__start_guest / homelab__restart_guest are on the brain's always-ask list). The Proxmox
# token has VM.PowerMgmt on exactly these, via the ArtyHeal role (see provision.sh).
SELF_VMID = int(os.environ.get("ARTY_VMID", "104"))
PORT_TIMEOUT = 5


def _guests() -> list[dict]:
    """Every VM and container with its fixed address and hardware address, where the config says."""
    found = []
    for item in _pve("GET", "/cluster/resources", {"type": "vm"}):
        kind = "lxc" if item["type"] == "lxc" else "qemu"
        config = _pve("GET", f"/nodes/{PVE_NODE}/{kind}/{item['vmid']}/config")
        net = str(config.get("net0", ""))
        ip = re.search(r"ip=(\d+\.\d+\.\d+\.\d+)", net) or re.search(r"ip=(\d+\.\d+\.\d+\.\d+)", str(config.get("ipconfig0", "")))
        mac = re.search(r"(?:hwaddr|virtio|e1000|vmxnet3)=([0-9A-Fa-f:]{17})", net)
        found.append({"vmid": item["vmid"], "kind": kind, "name": item.get("name", ""), "status": item.get("status", ""),
                      "ip": ip.group(1) if ip else None, "mac": mac.group(1).lower() if mac else None})
    return found


def _port_open(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=PORT_TIMEOUT):
            return True
    except OSError:
        return False


def _mac_answering(address: str) -> str | None:
    """The hardware address that answers for an IP on the local network, from the ARP table after a ping."""
    subprocess.run(["ping", "-c", "1", "-W", "1", address], capture_output=True, timeout=5)
    for line in open("/proc/net/arp").read().splitlines()[1:]:
        parts = line.split()
        if parts and parts[0] == address and parts[3] != "00:00:00:00:00:00":
            return parts[3].lower()
    return None


@mcp.tool()
def diagnose(address: str, port: int = 0, guest_name: str = "") -> str:
    """Work out why a homelab service at an address (and port) isn't answering, and what would fix it.
    Returns JSON: {"summary": "...", "fix": {"action": "start"|"restart", "vmid": N, "name": "..."} or null}. Read-only."""
    try:
        guests = _guests()
    except RuntimeError as error:
        return json.dumps({"summary": f"Couldn't read Proxmox: {error}", "fix": None})
    guest = next((g for g in guests if g["ip"] == address), None) or \
        next((g for g in guests if guest_name and g["name"].lower() == guest_name.lower()), None)
    where = f"{address}:{port}" if port else address
    if guest is None:
        reachable = _port_open(address, port) if port else _mac_answering(address) is not None
        return json.dumps({"summary": f"{where} isn't one of the Proxmox guests (it's {'answering' if reachable else 'not answering'} now). "
                                      "It may be an outside service or another device, so there's nothing for me to restart.", "fix": None})
    label = f"{'CT' if guest['kind'] == 'lxc' else 'VM'} {guest['vmid']} {guest['name']}"
    if guest["vmid"] == SELF_VMID:
        return json.dumps({"summary": f"{where} is my own container ({label}), so I can't fix it from here.", "fix": None})
    if guest["status"] != "running":
        return json.dumps({"summary": f"{label} is {guest['status']}, so {where} can't answer. Starting it should fix it.",
                           "fix": {"action": "start", "vmid": guest["vmid"], "name": label}})
    if port and _port_open(address, port):
        return json.dumps({"summary": f"{where} ({label}) is answering again now; it may have been a blip.", "fix": None})
    answering = _mac_answering(address)
    if answering and guest["mac"] and answering != guest["mac"]:
        return json.dumps({"summary": f"Another device (hardware address {answering}) is using {address}, the address {label} is set to: "
                                      "an address clash, usually the router giving it to something else. Restarting won't help; "
                                      "the guest needs a different address or a reservation in the router.", "fix": None})
    if answering is None:
        summary = f"{label} is running but not answering on the network at all."
    else:
        summary = f"{label} is running and on the network, but {where} isn't answering: the service inside has probably stopped or hung."
    return json.dumps({"summary": summary + " Restarting it should fix it.", "fix": {"action": "restart", "vmid": guest["vmid"], "name": label}})


def _power(vmid: int, action: str) -> str:
    vmid = int(vmid)
    if vmid == SELF_VMID:
        return "That's my own container; I won't restart myself."
    guest = next((g for g in _guests() if g["vmid"] == vmid), None)
    if guest is None:
        return f"There's no guest {vmid}."
    try:
        _pve("POST", f"/nodes/{PVE_NODE}/{guest['kind']}/{vmid}/status/{'start' if action == 'start' else 'reboot'}")
    except RuntimeError as error:
        return f"Proxmox refused to {action} {vmid}: {error}"
    return f"Asked Proxmox to {action} {guest['name']} ({vmid}). It usually takes under a minute to come back."


@mcp.tool()
def start_guest(vmid: int) -> str:
    """Start a stopped VM or container (never Arty's own). Needs the user's approval."""
    return _power(vmid, "start")


@mcp.tool()
def restart_guest(vmid: int) -> str:
    """Restart (reboot) a running VM or container that has hung (never Arty's own). Needs the user's approval."""
    return _power(vmid, "restart")


def _puffer_access_token() -> str:
    if _puffer_token["value"] and time.time() < _puffer_token["expires"]:
        return _puffer_token["value"]
    if not (PUFFER_CLIENT_ID and PUFFER_CLIENT_SECRET):
        raise RuntimeError("PufferPanel access isn't set up (PUFFERPANEL_CLIENT_ID / PUFFERPANEL_CLIENT_SECRET missing).")
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": PUFFER_CLIENT_ID,
                                   "client_secret": PUFFER_CLIENT_SECRET}).encode()
    request = urllib.request.Request(f"{PUFFER_URL}/oauth2/token", data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"PufferPanel refused the login ({error.code}); check the OAuth client id and secret.") from None
    if not body.get("access_token"):
        raise RuntimeError(f"PufferPanel gave no token: {body.get('error_description') or body.get('error') or 'no reason given'}")
    _puffer_token["value"] = body["access_token"]
    _puffer_token["expires"] = time.time() + int(body.get("expires_in") or 0) - TOKEN_REFRESH_MARGIN_SECONDS
    return _puffer_token["value"]


def _puffer(method: str, path: str):
    request = urllib.request.Request(f"{PUFFER_URL}/api{path}", method=method, data=b"" if method == "POST" else None,
                                     headers={"Authorization": f"Bearer {_puffer_access_token()}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            _puffer_token["value"] = ""
        raise RuntimeError(f"PufferPanel said {error.code}: {error.read().decode(errors='replace')[:200]}") from None
    return json.loads(raw) if raw.strip() else None


def _puffer_servers() -> list[dict]:
    return (_puffer("GET", "/servers?limit=100") or {}).get("servers") or []


def _puffer_state(server_id: str) -> str:
    try:
        running = _puffer("GET", f"/servers/{server_id}/status") or {}
    except RuntimeError:
        return "unknown"
    if running.get("installing"):
        return "installing"
    return "running" if running.get("running") else "stopped"


def find_puffer_server(servers: list[dict], wanted: str) -> dict | str:
    key = wanted.strip().lower()
    if not key:
        return "Say which server."
    for rule in (lambda s: s["id"].lower() == key or s["name"].lower() == key,
                 lambda s: s["id"].lower().startswith(key),
                 lambda s: key in s["name"].lower()):
        matches = [server for server in servers if rule(server)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return f"'{wanted}' matches more than one server: {', '.join(s['name'] for s in matches)}."
    names = ", ".join(s["name"] for s in servers) or "none"
    return f"There's no game server called '{wanted}'. The servers are: {names}."


@mcp.tool()
def pufferpanel_servers() -> str:
    """List the game servers on PufferPanel (the Minecraft servers) with their port and whether each is running."""
    try:
        servers = _puffer_servers()
    except RuntimeError as error:
        return str(error)
    if not servers:
        return "PufferPanel has no servers."
    lines = [f"{s['name']} (id {s['id']}, port {s.get('port') or '?'}): {_puffer_state(s['id'])}" for s in servers]
    return "\n".join(lines)


@mcp.tool()
def pufferpanel_power(server: str, action: str) -> str:
    """Start, stop or restart one PufferPanel game server, by name (e.g. "A+J") or id. Needs the user's approval."""
    action = action.strip().lower()
    if action not in PUFFER_ACTIONS:
        return f"Action must be one of {', '.join(PUFFER_ACTIONS)}."
    try:
        found = find_puffer_server(_puffer_servers(), server)
        if isinstance(found, str):
            return found
        _puffer("POST", f"/servers/{found['id']}/{action}")
    except RuntimeError as error:
        return f"Couldn't {action} {server}: {error}"
    return f"Asked PufferPanel to {action} {found['name']}. Minecraft takes a minute or so; check with pufferpanel_servers."


def _desktop_snapshots() -> list[dict]:
    snapshots = _pve("GET", f"/nodes/{PVE_NODE}/qemu/{DESKTOP_VMID}/snapshot")
    return sorted((s for s in snapshots if s.get("name") != "current"), key=lambda s: s.get("snaptime", 0))


@mcp.tool()
def snapshot_desktop(note: str = "") -> str:
    """Take a snapshot of your own desktop computer (VM 105) so it can be rolled back if something goes wrong.
    Do this before risky changes. Only the newest few of your snapshots are kept."""
    name = f"{SNAPSHOT_PREFIX}{time.strftime('%Y%m%d_%H%M%S')}"
    try:
        _pve("POST", f"/nodes/{PVE_NODE}/qemu/{DESKTOP_VMID}/snapshot", {"snapname": name, "description": (note or "taken by Arty")[:200], "vmstate": 0})
        deadline = time.time() + 120
        while time.time() < deadline and not any(s["name"] == name for s in _desktop_snapshots()):
            time.sleep(3)
        mine = [s for s in _desktop_snapshots() if s["name"].startswith(SNAPSHOT_PREFIX)]
        for old in mine[:-SNAPSHOTS_KEPT]:
            _pve("DELETE", f"/nodes/{PVE_NODE}/qemu/{DESKTOP_VMID}/snapshot/{old['name']}")
    except RuntimeError as error:
        return f"snapshot failed: {error}"
    return f"snapshot {name} taken. You can roll back in Proxmox: VM {DESKTOP_VMID} > Snapshots."


@mcp.tool()
def list_desktop_snapshots() -> str:
    """List the snapshots of your desktop computer (VM 105)."""
    try:
        snapshots = _desktop_snapshots()
    except RuntimeError as error:
        return str(error)
    if not snapshots:
        return "No snapshots."
    return "\n".join(f"{s['name']}: {time.strftime('%Y-%m-%d %H:%M', time.localtime(s.get('snaptime', 0)))} {s.get('description', '').strip()}" for s in snapshots)


@mcp.tool(description="Whether the user's websites and services are up, from Uptime Kuma: each monitor's state and response time.")
def service_status() -> str:
    if not KUMA_KEY:
        # Small models keep retrying a tool that only says "not set up", so answer with what is available.
        return ("Uptime Kuma isn't connected yet (it needs an API key), so here is the Proxmox server status instead:\n"
                + proxmox_status())
    request = urllib.request.Request(f"{KUMA_URL}/metrics", headers={"Authorization": "Basic " + base64.b64encode(f":{KUMA_KEY}".encode()).decode()})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            metrics = response.read().decode()
    except (urllib.error.URLError, TimeoutError) as error:
        return f"could not reach Uptime Kuma: {error}"
    states, times = {}, {}
    for line in metrics.splitlines():
        match = re.match(r'(monitor_status|monitor_response_time)\{([^}]*)\}\s+(\S+)', line)
        if not match:
            continue
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', match.group(2)))
        name = labels.get("monitor_name", "?")
        (states if match.group(1) == "monitor_status" else times)[name] = match.group(3)
    if not states:
        return "Uptime Kuma has no monitors."
    rows = sorted(states.items(), key=lambda item: (item[1] == "1", item[0].lower()))
    return "\n".join(f"{name}: {KUMA_STATES.get(state, state)}" + (f", {float(times[name]):.0f} ms" if name in times and times[name] not in ("-1", "NaN") else "") for name, state in rows)


def _dav_path(path: str) -> str:
    clean = "/".join(part for part in (path or "").strip().split("/") if part and part != "..")
    return f"/remote.php/dav/files/{urllib.parse.quote(NC_USER)}/" + urllib.parse.quote(clean)


def _dav(method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> bytes:
    if not (NC_USER and NC_PASSWORD):
        raise RuntimeError("Nextcloud isn't set up (NEXTCLOUD_USER / NEXTCLOUD_APP_PASSWORD missing).")
    auth = base64.b64encode(f"{NC_USER}:{NC_PASSWORD}".encode()).decode()
    request = urllib.request.Request(NC_URL + path, data=body, method=method, headers={"Authorization": f"Basic {auth}", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise RuntimeError("not found in Nextcloud") from None
        raise RuntimeError(f"Nextcloud said {error.code}") from None


def _dav_entries(xml_body: bytes, base: str) -> list[str]:
    rows = []
    for response in ElementTree.fromstring(xml_body).findall("d:response", DAV_NS):
        href = urllib.parse.unquote(response.findtext("d:href", "", DAV_NS))
        relative = href.split(f"/remote.php/dav/files/{NC_USER}/", 1)[-1]
        if relative.rstrip("/") == base.rstrip("/"):
            continue
        prop = response.find("d:propstat/d:prop", DAV_NS)
        is_folder = prop is not None and prop.find("d:resourcetype/d:collection", DAV_NS) is not None
        size = prop.findtext("oc:size", "", DAV_NS) if prop is not None else ""
        modified = prop.findtext("d:getlastmodified", "", DAV_NS) if prop is not None else ""
        rows.append(f"{relative}{'/' if is_folder and not relative.endswith('/') else ''}  {'' if is_folder else _gib(int(size or 0)) if int(size or 0) > GIB else f'{int(size or 0) // 1024} KB'}  {modified[5:16]}")
    return rows


@mcp.tool(description="List a folder in Nextcloud (the user's cloud drive). Shows only what the user has shared with you, "
                       "plus your own Arty folder.")
def nextcloud_list(folder: str = "") -> str:
    propfind = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:resourcetype/><oc:size/><d:getlastmodified/></d:prop></d:propfind>'
    try:
        body = _dav("PROPFIND", _dav_path(folder), propfind, {"Depth": "1", "Content-Type": "application/xml"})
    except RuntimeError as error:
        return str(error)
    rows = _dav_entries(body, folder.strip("/"))
    return "\n".join(rows) or "(empty folder)"


@mcp.tool()
def nextcloud_search(name: str) -> str:
    """Find files in Nextcloud whose name contains the given text."""
    pattern = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("%", "")
    search = (
        '<?xml version="1.0"?><d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:basicsearch>'
        '<d:select><d:prop><d:resourcetype/><oc:size/><d:getlastmodified/></d:prop></d:select>'
        f'<d:from><d:scope><d:href>/files/{NC_USER}</d:href><d:depth>infinity</d:depth></d:scope></d:from>'
        f'<d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>%{pattern}%</d:literal></d:like></d:where>'
        '<d:limit><d:nresults>50</d:nresults></d:limit></d:basicsearch></d:searchrequest>'
    ).encode()
    try:
        body = _dav("SEARCH", "/remote.php/dav/", search, {"Content-Type": "text/xml"})
    except RuntimeError as error:
        return str(error)
    return "\n".join(_dav_entries(body, "")) or f"No files named like '{name}'."


@mcp.tool()
def nextcloud_get(path: str) -> str:
    """Copy a file from Nextcloud onto your computer (into ~/nextcloud/) and return its text if it's a document.
    The local copy can then be opened, edited or read further with read_document."""
    try:
        content = _dav("GET", _dav_path(path))
    except RuntimeError as error:
        return str(error)
    relative = "/".join(part for part in path.strip("/").split("/") if part and part != "..")
    local = f"{NC_LOCAL_DIR}/{relative}"
    code, output = ssh(f'mkdir -p "$(dirname {quoted(local)})" && cat > {quoted(local)}', stdin=content, timeout=300)
    if code != 0:
        return f"could not save it on your computer: {output.decode(errors='replace')[:200]}"
    code, extracted = remote_text(f"doc2text {quoted(local)}", timeout=600)
    preview = clip(extracted, 4000) if code == 0 and extracted.strip() else "(not a readable document)"
    return f"Saved to {local} ({len(content) // 1024} KB).\n\n{preview}"


@mcp.tool(description="Upload a file from your computer to the user's Nextcloud, into the shared 'Arty' folder "
                       "(relative paths are in ~/projects).")
def nextcloud_upload(local_path: str, name: str = "") -> str:
    code, content = ssh(f"cat {quoted(local_path)}", timeout=300)
    if code != 0:
        return f"could not read {local_path}: {content.decode(errors='replace')[:200]}"
    filename = os.path.basename(name or local_path.rstrip("/")) or "upload"
    try:
        try:
            _dav("MKCOL", _dav_path(NC_UPLOAD_FOLDER))
        except RuntimeError as error:
            if "405" not in str(error):
                raise
        _dav("PUT", _dav_path(f"{NC_UPLOAD_FOLDER}/{filename}"), content)
    except RuntimeError as error:
        return f"upload failed: {error}"
    return f"Uploaded to Nextcloud: {NC_UPLOAD_FOLDER}/{filename} ({len(content) // 1024} KB)"


if __name__ == "__main__":
    mcp.run()
