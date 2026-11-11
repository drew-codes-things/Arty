import asyncio
import re

import aiohttp

from player_names import YOUTUBE_NAMES

YOUTUBE_RESULTS = "https://www.youtube.com/results"
YOUTUBE_WATCH = "https://www.youtube.com/watch?v="
SEARCH_TIMEOUT_SECONDS = 10
RESULT_VIDEO_ID = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"')
ANY_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Cookie": "CONSENT=YES+1; SOCS=CAI",
    "Accept-Language": "en-GB,en;q=0.9",
}


def is_youtube(app: str) -> bool:
    return " ".join(app.lower().split()) in YOUTUBE_NAMES


async def youtube_watch_url(http, query: str) -> str | None:
    if http is None:
        return None
    try:
        async with http.get(YOUTUBE_RESULTS, params={"search_query": query}, headers=BROWSER_HEADERS,
                            timeout=aiohttp.ClientTimeout(total=SEARCH_TIMEOUT_SECONDS)) as response:
            if response.status != 200:
                return None
            page = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
    found = RESULT_VIDEO_ID.search(page) or ANY_VIDEO_ID.search(page)
    return YOUTUBE_WATCH + found.group(1) if found else None
