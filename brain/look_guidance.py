import re

APPEARANCE_PATTERN = (r"\bhow (?:(?:do|does|did|will|would) )?(?:i|my)\b.{0,40}?\blook\b(?!\s+(?:up|for|into|at|after|through|out|around)\b)"
                      r"|\b(?:do|will|would) i look\b(?!\s+(?:up|for|into|at|after|through|out|around)\b)"
                      r"|\bwhat (?:do|does) i look like\b"
                      r"|\b(?:outfit|wearing|dressed|clothes|clothing|shirt|jacket|tie|hair|haircut|hairstyle|beard|shave|makeup|skin|"
                      r"posture|slouch\w*|grooming|groomed|appearance|lighting)\b"
                      r"|\bbackground (?:for|ok|okay|good|tidy|messy)\b"
                      r"|\b(?:for|on|in) (?:a|my|the) (?:video )?(?:call|meeting|interview|zoom|stream)\b"
                      r"|\b(?:does|do|will) (?:this|these|it) (?:match|go with|suit)\b")
APPEARANCE_QUESTION = re.compile(APPEARANCE_PATTERN, re.IGNORECASE)
APPEARANCE_GUIDANCE = ("They are asking how they look, so give honest, specific, kind and practical feedback: say what works, "
                       "what to change, and one or two concrete suggestions they can act on now. Talk only about what is visible: "
                       "clothes, hair, grooming, posture, lighting, framing and what is behind them. Never guess their identity, "
                       "age, ethnicity, health or any other sensitive trait, never rate how attractive they are with a number or a "
                       "score, and never compare them to other people.")


def is_appearance_question(question: str) -> bool:
    return bool(APPEARANCE_QUESTION.search(question or ""))


def with_guidance(prompt: str, question: str) -> str:
    return f"{prompt} {APPEARANCE_GUIDANCE}" if is_appearance_question(question) else prompt
