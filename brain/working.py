import logging
import re
from datetime import datetime

import code_sandbox
import connections
import question_kinds

log = logging.getLogger("brain.working")

TOOL_NAME = "work_out"
RUN_SECONDS = 20
MAX_TRIES = 2
SHOWN_OUTPUT_CHARS = 1500
STEP_LINE_CHARS = 200
ANSWER_PREFIX = "Answer:"
HAS_A_NUMBER = re.compile(r"\d")
NOTHING_TO_WORK_OUT = "Nothing in the message needs working out exactly."
COULD_NOT_WORK_OUT = "Couldn't work it out exactly this time, so answer it carefully yourself."
PROGRAM_PROMPT = """Write a short Python 3 program that works out the answer to the question below exactly and prints it.
- Today is {today}. Use datetime for dates and times, and Decimal or Fraction for money, so nothing is rounded early.
- Print each step on its own line in plain words with its number, then the final answer on the last line as "Answer: ..."
  with its units, rounded the way the question asks (money to the penny).
- Only the standard library. No input(), files or network.
Reply with only the program, in one ```python block.

Question: {question}"""
RETRY_NOTE = """

Your last program didn't work:
{problem}
Write it again, fixed."""
WORKED_OUT = ("Worked out exactly by running a program. Use these results as they are and don't redo the sums in your "
              "head; give the answer plainly and show the key steps only if that helps:\n{printed}")


def today_text() -> str:
    return datetime.now().strftime("%A %-d %B %Y")


def program_in(reply: str) -> str:
    blocks = [code for tag, code in code_sandbox.fenced_blocks(reply) if code.strip()]
    return blocks[0] if blocks else ""


def printed_answer(program: str) -> tuple[str | None, str]:
    unit = code_sandbox.Unit("python", program, input_excuse=False)
    verdict = code_sandbox.checked_unit(unit, RUN_SECONDS)
    printed = code_sandbox.printed_by(verdict)
    if printed and any(line.strip().startswith(ANSWER_PREFIX) for line in printed.splitlines()):
        return printed.strip(), ""
    return None, code_sandbox.problem_text(verdict, code_sandbox.PROBLEM_CHARS) or "it printed no Answer: line"


def shown_steps(printed: str) -> str:
    lines = [line.strip()[:STEP_LINE_CHARS] for line in printed.splitlines()]
    answers = [line for line in lines if line.startswith(ANSWER_PREFIX)]
    steps = [line for line in lines if not line.startswith(ANSWER_PREFIX) and HAS_A_NUMBER.search(line)]
    return "\n".join(steps + answers[-1:])[-SHOWN_OUTPUT_CHARS:]


def work_out(text: str, complete=None) -> str:
    question = str(text or "").strip()
    if len(question) > question_kinds.QUESTION_CHARS:
        return NOTHING_TO_WORK_OUT
    if not question_kinds.needs_exact_work(question) or not code_sandbox.is_on():
        return NOTHING_TO_WORK_OUT
    write = complete or connections.classify_text
    prompt = PROGRAM_PROMPT.format(today=today_text(), question=question)
    problem = ""
    for _ in range(MAX_TRIES):
        program = program_in(write(prompt + (RETRY_NOTE.format(problem=problem) if problem else "")))
        if not program:
            problem = "there was no ```python block"
            continue
        try:
            printed, problem = printed_answer(program)
        except code_sandbox.RunnerUnavailable as error:
            log.warning("couldn't work out %r exactly: %s", question[:80], error)
            return COULD_NOT_WORK_OUT
        if printed:
            log.info("worked out %r exactly with a program", question[:80])
            return WORKED_OUT.format(printed=shown_steps(printed))
    log.warning("couldn't work out %r exactly: %s", question[:80], problem[:200])
    return COULD_NOT_WORK_OUT


def found_nothing_to_work_out(name: str, result) -> bool:
    return name == TOOL_NAME and str(result).startswith((NOTHING_TO_WORK_OUT, COULD_NOT_WORK_OUT))


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = {
    TOOL_NAME: (lambda arguments: work_out(arguments.get("text")), _tool(
        TOOL_NAME, "Work out a maths, money, measurement or date question exactly by writing and running a small program, "
                   "instead of doing the sums in your head. Give it the whole question in words.",
        {"text": {"type": "string", "description": "the question with all its numbers, e.g. the user's message"}}, ["text"])),
}
