import re

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


def parse(command: str) -> dict | None:
    text = " ".join(command.split())
    for pattern in (SPOTIFY_PLAY, SPOTIFY_PLAY_LEADING):
        if match := pattern.match(text):
            query = match.group("query").strip(" ,.")
            return {"action": "spotify_play", "query": query} if query else None
    for pattern, action in MEDIA_COMMANDS:
        if pattern.match(text):
            return {"action": action}
    return None
