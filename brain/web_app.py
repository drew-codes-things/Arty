import os
from pathlib import Path

PREFIX = "ios"
INDEX = "index.html"
ROOT = Path(os.environ.get("ARTY_IOS_DIR", Path(__file__).resolve().parents[1] / "apps" / "beta" / "ios"))
NOT_SERVED = {"tests"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
}
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'", "script-src 'self'", "style-src 'self'", "font-src 'self'", "connect-src 'self'",
    "img-src 'self' blob: data:", "media-src 'self' blob:", "worker-src 'self'", "manifest-src 'self'",
    "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'",
))
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def find(segments: list[str], root: Path | None = None) -> Path | None:
    folder = (root or ROOT).resolve()
    relative = segments[1:] or [INDEX]
    if relative[0] in NOT_SERVED or any(part.startswith(".") for part in relative):
        return None
    candidate = folder.joinpath(*relative).resolve()
    if not candidate.is_relative_to(folder) or not candidate.is_file() or candidate.suffix not in CONTENT_TYPES:
        return None
    return candidate


def content_type(path: Path) -> str:
    return CONTENT_TYPES[path.suffix]
