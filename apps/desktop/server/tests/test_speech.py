"""Actions should be performed, not spoken. Emphasis should still be spoken."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process.text_func.speech import SentenceSplitter, split_reply

CASES = [
    ("*snaps* fine, it's whatever.", ["fine"], ["snaps"], "emote"),
    ("*sighs* i know.", ["i know"], ["sighs"], "sigh"),
    ("*shrugs* dunno.", ["dunno"], ["shrugs"], "shrug"),
    ("*blinks* wait, what?", ["wait"], ["blinks"], None),

    ("that edit took *so long* to render.", ["so long", "render"], [], None),
    ("i said *nothing* about that.", ["nothing"], [], None),
    ("it's *that* bad.", ["that"], [], None),
    ("this is *really* annoying.", ["really"], [], None),

    ("*tilts her head* you sure about that?", ["you sure"], ["tilts"], "tilt"),
    ("*rolls her eyes* obviously.", ["obviously"], ["rolls"], "eyeroll"),

    ("[leans back] nope.", ["nope"], ["leans back"], "lean"),
    ("i think (probably) yes.", ["probably"], [], None),

    ("**bold** and `code` and \U0001F642 done.", ["done"], ["`", "*"], None),
    ("*unclosed action here", [], ["unclosed"], None),

    ("\\*sighs\\* fine, whatever.", ["fine"], ["sighs", "\\"], "sigh"),
]


STREAM_CASES = [
    "*sighs* fine. i'll look at it. but you owe me, seriously.",
    "it rendered at 3.5 fps which is, frankly, an insult. *rolls her eyes*",
    "*tilts her head* wait. you did what?",
    "no. absolutely not.",
    "hi",
    "yeah ok so the thing is i've been messing with this export all afternoon "
    "and it keeps desyncing about four seconds in and i cannot work out why",
]


def check_stream(failures):
    for reply in STREAM_CASES:
        for size in (1, 4, 13):
            splitter = SentenceSplitter()
            segments = []
            for i in range(0, len(reply), size):
                segments += splitter.feed(reply[i:i + size])
            tail = splitter.flush()
            if tail:
                segments.append(tail)

            rejoined = " ".join(segments)
            if rejoined.split() != reply.split():
                failures.append(
                    f"{reply!r} at delta={size}\n      text changed: {rejoined!r}")
            for segment in segments:
                if segment.count("*") % 2:
                    failures.append(
                        f"{reply!r} at delta={size}\n      cut inside an action: {segment!r}")


def run():
    failures = []
    check_stream(failures)
    for reply, must, must_not, animation in CASES:
        out = split_reply(reply)
        spoken = out["speech"].lower()

        for phrase in must:
            if phrase.lower() not in spoken:
                failures.append(f"{reply!r}\n      missing {phrase!r} from speech {out['speech']!r}")
        for phrase in must_not:
            if phrase.lower() in spoken:
                failures.append(f"{reply!r}\n      spoke {phrase!r}, should have been performed: {out['speech']!r}")
        if animation:
            got = [c["animation"] for c in out["cues"]]
            if animation not in got:
                failures.append(f"{reply!r}\n      expected a {animation!r} cue, got {got}")

    print(f"  {len(CASES)} split cases + {len(STREAM_CASES) * 3} streaming cases, "
          f"{len(failures)} failures")
    for f in failures:
        print("   FAIL", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())


def test_numbers_in_brackets_are_said_not_dropped():
    parts = split_reply("The answer to 5 times 6 is [30].")
    assert parts["speech"] == "The answer to 5 times 6 is 30."
    assert parts["cues"] == []


def test_a_quoted_draft_is_shown_with_its_lines_but_not_spoken():
    parts = split_reply("Here's a draft post. It's here in the chat to copy:\n\n> I built an AI assistant.\n>\n> #AI #Homelab")
    assert parts["speech"] == "Here's a draft post. It's here in the chat to copy:"
    assert parts["display"].endswith("I built an AI assistant.\n\n#AI #Homelab")
    assert parts["quoted"]
    only_draft = split_reply("> Thanks for reading.\n> #AI")
    assert only_draft["speech"] == "" and only_draft["display"] == "Thanks for reading.\n#AI"


def test_a_long_quoted_line_is_never_cut_mid_way():
    splitter = SentenceSplitter()
    draft = "Here's a draft post for you to copy:\n\n> " + "This sentence is part of the draft. " * 15 + "\n> #AI\n"
    pieces = splitter.feed(draft) + [splitter.flush()]
    pieces = [p for p in pieces if p]
    spoken = [split_reply(p)["speech"] for p in pieces]
    assert all("part of the draft" not in s for s in spoken), spoken
    assert any("part of the draft" in split_reply(p)["display"] for p in pieces)


def test_dashes_from_the_model_still_count_as_places_to_pause():
    from process.text_func import speech
    for dash in ("\u2014", "\u2013"):
        assert speech._SOFT_BREAK.match(f"{dash} ") is not None
