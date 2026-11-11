import re

from player_names import YOUTUBE_NAMES

FILLER = r"(?:please |can you |could you |would you |just )*"
END = r"[\s.!?,]*(?:please)?[\s.!?]*$"
MUSIC = r"(?: (?:the |my )?(?:music|song|track|spotify|it|playback))?"

SPOTIFY_PLAY = re.compile(rf"^{FILLER}(?:play|put on|stick on|start)\s+(?P<query>.+?)\s+(?:on|from|in|using|with)\s+spotify{END}",
                          re.IGNORECASE)
SPOTIFY_PLAY_LEADING = re.compile(rf"^{FILLER}spotify,?\s+(?:play|put on)\s+(?P<query>.+?){END}", re.IGNORECASE)
MEDIA_COMMANDS = [
    (re.compile(rf"^{FILLER}(?:pause|stop){MUSIC}(?: on spotify)?{END}", re.IGNORECASE), "media_pause"),
    (re.compile(rf"^{FILLER}(?:resume|unpause|continue|carry on with){MUSIC}(?: on spotify)?{END}", re.IGNORECASE), "media_play"),
    (re.compile(rf"^{FILLER}(?:play|start)(?: the| my)? (?:music|spotify){END}", re.IGNORECASE), "media_play"),
    (re.compile(rf"^{FILLER}(?:next|skip)(?: (?:this |the )?(?:song|track|one))?(?: on spotify)?{END}", re.IGNORECASE), "media_next"),
    (re.compile(rf"^{FILLER}(?:previous|go back|last)(?: (?:song|track|one))?(?: on spotify)?{END}", re.IGNORECASE), "media_previous"),
    (re.compile(rf"^{FILLER}(?:turn (?:it|the music|the volume) up|volume up|louder){END}", re.IGNORECASE), "volume_up"),
    (re.compile(rf"^{FILLER}(?:turn (?:it|the music|the volume) down|volume down|quieter){END}", re.IGNORECASE), "volume_down"),
]

PLAY_VERB = r"(?:play|put on|stick on)"
PLAYER_SUFFIX = r"(?: (?:on|in|with|from) .+?)?"
PLAY_SKIP_COMMANDS = [
    (re.compile(rf"^{FILLER}play (?:the )?next (?:song|track|one){PLAYER_SUFFIX}{END}", re.IGNORECASE), "media_next"),
    (re.compile(rf"^{FILLER}play (?:the )?(?:previous|last) (?:song|track|one){PLAYER_SUFFIX}{END}", re.IGNORECASE), "media_previous"),
]
PLAY_ON_APP = re.compile(rf"^{FILLER}{PLAY_VERB}\s+(?P<query>.+)\s+(?:on|from|in|using)\s+(?P<app>.+?){END}", re.IGNORECASE)
PLAY_LEADING_YOUTUBE = re.compile(rf"^{FILLER}(?P<app>youtube(?: music)?),?\s+{PLAY_VERB}\s+(?P<query>.+?){END}", re.IGNORECASE)
PLAY_BARE = re.compile(rf"^{FILLER}{PLAY_VERB}\s+(?P<query>.+?){END}", re.IGNORECASE)
SONG_HINTS = re.compile(r"\b(?:by|playlist|album|song|track|mix|music|radio)\b", re.IGNORECASE)
KNOWN_PLAYERS = YOUTUBE_NAMES | {"deezer", "tidal", "soundcloud", "apple music", "amazon music", "pandora", "plex", "vlc", "netflix",
                                  "twitch", "prime video", "disney plus", "tiktok"}
VAGUE_QUERIES = {"it", "that", "this", "something", "anything", "one"}
APP_PREFIX = re.compile(r"^(?:the|my)\s+", re.IGNORECASE)
APP_SUFFIX = re.compile(r"\s+(?:app|application)$", re.IGNORECASE)
DEFAULT_MUSIC_APP = "YouTube"

OPEN_APP = re.compile(rf"^{FILLER}(?:open(?: up)?|launch|start up|load up|bring up)\s+(?:the |my )?(?P<app>.+?)(?: app| application)?{END}",
                      re.IGNORECASE)

VOLUME_LEVEL = re.compile(rf"^{FILLER}(?:set |turn |put )?(?:the )?volume (?:to |at |on )?(?P<level>\d{{1,3}})(?: ?%| percent)?{END}",
                          re.IGNORECASE)
VOLUME_CHANGES = [
    (re.compile(rf"^{FILLER}(?:turn (?:it|the music|the volume|the sound) up|volume up|louder|speak up)(?: a (?:bit|little|touch))?{END}", re.IGNORECASE), "up"),
    (re.compile(rf"^{FILLER}(?:turn (?:it|the music|the volume|the sound) down|volume down|quieter|softer)(?: a (?:bit|little|touch))?{END}",
                re.IGNORECASE), "down"),
    (re.compile(rf"^{FILLER}unmute(?: (?:the )?(?:speakers?|sound|volume|yourself))?{END}", re.IGNORECASE), "unmute"),
    (re.compile(rf"^{FILLER}mute(?: (?:the )?(?:speakers?|sound|volume|yourself))?{END}", re.IGNORECASE), "mute"),
]


def parse_volume(command: str) -> dict | None:
    text = " ".join(command.split())
    if match := VOLUME_LEVEL.match(text):
        return {"level": min(100, int(match.group("level")))}
    for pattern, change in VOLUME_CHANGES:
        if pattern.match(text):
            return {"change": change}
    return None


def parse_play(text: str) -> dict | None:
    if match := PLAY_LEADING_YOUTUBE.match(text):
        return play_on(match.group("app"), match.group("query"))
    if match := PLAY_ON_APP.match(text):
        name = APP_PREFIX.sub("", match.group("app").strip(" ,."))
        named_as_app = bool(APP_SUFFIX.search(name))
        name = APP_SUFFIX.sub("", name)
        if name.lower() == "spotify":
            return spotify_play(match.group("query"))
        if name.lower() in KNOWN_PLAYERS or named_as_app:
            return play_on(name, match.group("query"))
    if match := PLAY_BARE.match(text):
        query = match.group("query")
        if SONG_HINTS.search(query):
            return play_on(DEFAULT_MUSIC_APP, query)
    return None


def is_a_song_request(query: str) -> bool:
    return bool(query) and query.lower() not in VAGUE_QUERIES


def play_on(app: str, query: str) -> dict | None:
    query = query.strip(" ,.")
    return {"action": "play_on", "app": app.strip(" ,."), "query": query} if is_a_song_request(query) else None


def spotify_play(query: str) -> dict | None:
    query = query.strip(" ,.")
    return {"action": "spotify_play", "query": query} if is_a_song_request(query) else None


def parse(command: str) -> dict | None:
    text = " ".join(command.split())
    for pattern, action in PLAY_SKIP_COMMANDS:
        if pattern.match(text):
            return {"action": action}
    for pattern in (SPOTIFY_PLAY, SPOTIFY_PLAY_LEADING):
        if match := pattern.match(text):
            return spotify_play(match.group("query"))
    for pattern, action in MEDIA_COMMANDS:
        if pattern.match(text):
            return {"action": action}
    if play := parse_play(text):
        return play
    if match := OPEN_APP.match(text):
        app = match.group("app").strip(" ,.")
        return {"action": "open_app", "app": app} if app else None
    return None
