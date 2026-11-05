"""Send a screenshot to a vision model and get a spoken-style answer."""
import base64

from openai import OpenAI

from process import route
from process.config import load_config

_config = load_config()
_vision = _config.get("vision") or {}
_llm = _config.get("brain") or {}

ENABLED = bool(_vision.get("enabled", True))
MODEL = _vision.get("model", "qwen2.5vl:7b")
MAX_TOKENS = int(_vision.get("max_tokens", 300))

MAX_EDGE = int(_vision.get("max_edge", 1024))

DEFAULT_QUESTION = "What is on my screen right now?"

SYSTEM = """You are looking at a screenshot of the user's screen.

Answer their question about it in one to three short sentences, in a casual
spoken register. Your reply is read aloud by a text-to-speech voice.

Describe only what is actually visible. Never guess at content you cannot see,
and say so plainly if the screen is unclear. No markdown, no lists, no emoji.
If you notice something sensitive like a password or private message, say that
you would rather not read it out."""


class VisionError(RuntimeError):
    pass


_client = None


BASE_URL = (_vision.get("base_url") or _llm.get("base_url") or "").strip() or None
API_KEY = _vision.get("api_key") or _llm.get("api_key") or "not-needed"


def _endpoint():
    if BASE_URL and BASE_URL.rstrip("/") == route.LOCAL_URL:
        return route.base(), route.access_headers()
    return BASE_URL, {}


def client():
    global _client
    base_url, access = _endpoint()
    if _client is None or str(_client.base_url).rstrip("/") != (base_url or "").rstrip("/"):
        # Arty's brain sends images through its own agent unless asked for the vision model directly.
        _client = OpenAI(api_key=API_KEY, base_url=base_url, timeout=180.0, default_headers={"X-Arty-Direct-Vision": "1", **access})
    return _client


def describe_endpoint():
    return _endpoint()[0] or "https://api.openai.com/v1"


def _shrink(image_bytes):
    try:
        import io

        from PIL import Image
    except ImportError:
        return image_bytes, "image/jpeg"

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception:
        return image_bytes, "image/jpeg"

    if max(img.size) > MAX_EDGE:
        scale = MAX_EDGE / max(img.size)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.LANCZOS,
        )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=78)
    return buf.getvalue(), "image/jpeg"


def describe(image_bytes, question=None, mime="image/png"):
    if not ENABLED:
        raise VisionError("Screen vision is disabled in character_config.yaml.")
    if not image_bytes:
        raise VisionError("No screenshot was captured.")

    image_bytes, mime = _shrink(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    question = (question or "").strip() or DEFAULT_QUESTION

    try:
        completion = client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
            temperature=0.4,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
    except Exception as e:
        msg = str(e)
        if "context size" in msg or "exceed_context" in msg:
            raise VisionError(
                "The screenshot was too large for the model's context. "
                "Lower vision.max_edge in character_config.yaml."
            ) from e
        if "not found" in msg or "404" in msg:
            raise VisionError(
                f"Vision model '{MODEL}' isn't installed. Run: ollama pull {MODEL}"
            ) from e
        raise VisionError(f"Vision call failed: {type(e).__name__}: {msg[:200]}") from e

    return (completion.choices[0].message.content or "").strip()
