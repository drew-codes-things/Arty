import re

import media

MAX_LYRIC_SECONDS = 15 * 60
MIN_SPOKEN_WORDS = 12
MIN_LYRIC_WORDS = 4
MAX_LYRIC_WORDS = 14
LYRIC_LINES_SEARCHED = 2
RESULTS_COMPARED = 5
LINE_COVERED = 0.8
TITLES_AGREE = 0.5
TITLE_CHARS = 140
TRANSCRIPT_LINE = re.compile(r"^\[\d+:\d+(?::\d+)?\]\s*(.*)$")
WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
RESULT = re.compile(r"^\s*\d+\.\s+(?P<title>.*)\n\s+(?P<url>https?://\S+)\n\s+(?P<snippet>.*)$", re.MULTILINE)
TITLE_NOISE = {"lyrics", "lyric", "genius", "azlyrics", "musixmatch", "song", "songs", "by", "official", "video", "letra",
               "audio", "and", "the", "a", "of", "com", "translation", "meaning"}
SEARCH_FAILURES = ("unknown tool", "tool error", "tool unavailable", "search is unavailable", "No results")

NO_SOUND = "This file has no sound, so there is nothing to identify."
NO_LYRICS = ("I couldn't make out any sung words (it may be an instrumental), so I can't name this song from its "
             "lyrics.")
NO_TRANSCRIPT = "I couldn't transcribe it ({reason}), so I can't name this song from its lyrics."
LYRIC_MATCH_START = "Matched from the lyrics"
TOO_LONG_FOR_LYRICS = "This is too long to transcribe for a lyric search; send a clip of the song (a minute or two with singing in it)."
LYRICS_NOT_FOUND = ("I heard these words but no search result contained them, so I can't name the song and won't guess. "
                    "Lines I searched for: {lines}")


def words_of(text: str) -> list[str]:
    return [word.lower() for word in WORD.findall(text)]


def sung_lines(transcript: str) -> list[str]:
    lines = []
    for raw in transcript.splitlines():
        found = TRANSCRIPT_LINE.match(raw.strip())
        if found and found.group(1).strip():
            lines.append(found.group(1).strip())
    return lines


def distinctive_lines(transcript: str) -> list[str]:
    lines = sung_lines(transcript)
    counts: dict[str, int] = {}
    chosen: dict[str, str] = {}
    for line in lines:
        words = words_of(line)
        key = " ".join(words)
        counts[key] = counts.get(key, 0) + 1
        if MIN_LYRIC_WORDS <= len(words) <= MAX_LYRIC_WORDS:
            chosen.setdefault(key, line)
    ranked = sorted(chosen, key=lambda key: -(len(key.split()) + 2 * (counts[key] > 1)))
    return [chosen[key] for key in ranked[:LYRIC_LINES_SEARCHED]]


def search_results(text: str) -> list[dict]:
    if text.startswith(SEARCH_FAILURES):
        return []
    return [{"title": found["title"].strip(), "url": found["url"], "snippet": found["snippet"].strip()} for found in RESULT.finditer(text)]


def line_covered(line_words: list[str], result: dict) -> bool:
    present = set(words_of(result["title"] + " " + result["snippet"]))
    return bool(line_words) and sum(word in present for word in line_words) / len(line_words) >= LINE_COVERED


def title_key(title: str) -> set[str]:
    return {word for word in words_of(title) if word not in TITLE_NOISE}


def titles_agree(first: dict, second: dict) -> bool:
    one, two = title_key(first["title"]), title_key(second["title"])
    return bool(one and two) and len(one & two) / len(one | two) >= TITLES_AGREE


def lyric_confidence(matching: list[dict]) -> str:
    agreeing = [other for other in matching[1:] if titles_agree(matching[0], other)]
    if agreeing:
        return f"likely: {1 + len(agreeing)} search results with these words agree on the title"
    if len(matching) > 1:
        return "unsure: results with these words name different songs"
    return "unsure: only one search result had these words"


def best_lyric_match(lines: list[str], search) -> tuple[str, list[dict]] | None:
    best = None
    for line in lines:
        results = search_results(search(f'"{line}" lyrics'))[:RESULTS_COMPARED]
        words = words_of(line)
        matching = [result for result in results if line_covered(words, result)]
        if matching and (best is None or len(matching) > len(best[1])):
            best = (line, matching)
    return best


def identify_by_lyrics(transcript: str, search) -> str:
    if len(words_of(" ".join(sung_lines(transcript)))) < MIN_SPOKEN_WORDS:
        return NO_LYRICS
    lines = distinctive_lines(transcript)
    if not lines:
        return NO_LYRICS
    found = best_lyric_match(lines, search)
    if found is None:
        return LYRICS_NOT_FOUND.format(lines="; ".join(f'"{line}"' for line in lines))
    line, matching = found
    top = matching[0]
    return (f"{LYRIC_MATCH_START} (a web search for a line I transcribed; the transcript can mishear words):\n"
            f'Line searched: "{line}"\n'
            f'Best search result: "{top["title"][:TITLE_CHARS]}" ({top["url"]})\n'
            f"Confidence: {lyric_confidence(matching)}. The title is the page's own wording, so work out the song and artist "
            "from it and say it is probably that, not certainly.")


def lyric_answer(meta: dict, duration: float, search) -> str:
    if duration > MAX_LYRIC_SECONDS:
        return TOO_LONG_FOR_LYRICS
    try:
        transcript = media.transcript_of(meta)
    except media.MediaError as error:
        return NO_TRANSCRIPT.format(reason=error)
    return identify_by_lyrics(transcript, search)


def identify(meta: dict, search) -> str:
    info = media.probe(meta["path"])
    if not info["audio"]:
        return NO_SOUND
    return lyric_answer(meta, info["duration"], search)
