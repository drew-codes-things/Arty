import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doclib.search import search  # noqa: E402

TOP = 3
DEFAULT_SET = Path(__file__).resolve().parent / "retrieval_set.json"


def is_expected(url: str, expected: list[str]) -> bool:
    lowered = url.lower()
    return any(pattern.lower() in lowered for pattern in expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the right doc section is in the top results.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--verbose", action="store_true")
    options = parser.parse_args(argv)
    questions = json.loads(options.set.read_text())
    passed, timings = 0, []
    for item in questions:
        started = time.monotonic()
        hits = search(options.database, item["question"], options.top)
        timings.append(time.monotonic() - started)
        found = next((rank for rank, hit in enumerate(hits, start=1) if is_expected(hit.url, item["expect"])), 0)
        passed += bool(found)
        print(f"{'PASS' if found else 'FAIL'} {found or '-'} [{item['language']}] {item['question']}")
        if options.verbose or not found:
            for hit in hits:
                print(f"     {hit.score:6.3f} {hit.url}")
    languages = len({item["language"] for item in questions})
    print(f"top-{options.top}: {passed}/{len(questions)} across {languages} languages; "
          f"slowest search {max(timings):.2f} s, mean {sum(timings) / len(timings):.2f} s")
    return 0 if passed == len(questions) else 1


if __name__ == "__main__":
    sys.exit(main())
