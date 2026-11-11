import os


LISTEN_HOST = os.environ.get("BRAIN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("BRAIN_PORT", "8081"))
PUBLIC_URL = (os.environ.get("BRAIN_PUBLIC_URL") or f"http://127.0.0.1:{LISTEN_PORT}").rstrip("/")
HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CONFIG_FILE = os.environ.get("BRAIN_CONFIG", "/srv/ai/brain/brain.json")
TRUSTED_PROXIES = frozenset(address.strip() for address in os.environ.get("BRAIN_TRUSTED_PROXIES", "").split(",")
                            if address.strip())
STARFIRE_URL = os.environ.get("BRAIN_STARFIRE_URL", "")
