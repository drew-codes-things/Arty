import os


LISTEN_HOST = os.environ.get("BRAIN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("BRAIN_PORT", "8081"))
PUBLIC_URL = (os.environ.get("BRAIN_PUBLIC_URL") or f"http://127.0.0.1:{LISTEN_PORT}").rstrip("/")
HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8000")
ARTY_ENGINE_URL = os.environ.get("ARTY_ENGINE_URL", "http://127.0.0.1:11438")
CONFIG_FILE = os.environ.get("BRAIN_CONFIG", "/srv/ai/brain/brain.json")
TRUSTED_PROXIES = frozenset(address.strip() for address in os.environ.get("BRAIN_TRUSTED_PROXIES", "").split(",")
                            if address.strip())
STARFIRE_URL = os.environ.get("BRAIN_STARFIRE_URL", "")
ROOM_ADDRESSES = frozenset(address.strip() for address in os.environ.get("ROOM_ADDRESSES", "").split(",") if address.strip())
OWN_APPS_FILE = os.environ.get("BRAIN_OWN_APPS_FILE", "/srv/ai/brain/own-apps.json")
HOMELAB_NAMES = tuple(name.strip() for name in os.environ.get("HOMELAB_NAMES", "").split(",") if name.strip())
CODE_RUNNER_URL = os.environ.get("CODE_RUNNER_URL", "")
CODE_RUNNER_TOKEN = os.environ.get("CODE_RUNNER_TOKEN", "")
