import base64
import json
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent / "client"
TEMPLATES = {"debian": ("setup-debian.sh.template", "my-voice-setup.sh"),
             "windows": ("setup-windows.ps1.template", "my-voice-setup.ps1")}


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _safe_name(name: str, system: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_.()")
    return cleaned.replace("'", "").strip() or f"{system} PC"


def build_installer(system: str, hub_url: str, token: str, device_name: str) -> tuple[str, bytes]:
    template_name, filename = TEMPLATES[system]
    template = (CLIENT_DIR / template_name).read_text(encoding="utf-8")
    client = (CLIENT_DIR / "my_voice_client.py").read_bytes()
    config = json.dumps({"hub": hub_url, "token": token, "name": device_name}).encode()
    script = (template.replace("__DEVICE_NAME__", _safe_name(device_name, system))
                      .replace("__CLIENT_B64__", _encoded(client))
                      .replace("__CONFIG_B64__", _encoded(config)))
    if system == "windows":
        return filename, script.replace("\n", "\r\n").encode("utf-8-sig")
    return filename, script.encode()
