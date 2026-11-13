import json
import re
import sys
import urllib.request
from dataclasses import dataclass

FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([^\s`{]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.M | re.S)
TAGS = {
    "python": {"python", "py", "python3"}, "javascript": {"javascript", "js", "node", "mjs"}, "typescript": {"typescript", "ts"},
    "bash": {"bash", "sh", "shell"}, "c": {"c"}, "rust": {"rust", "rs"}, "go": {"go", "golang"}, "java": {"java"},
    "sql": {"sql", "sqlite", "sqlite3"},
}
MARKER = "@@hidden@@"
SHOW_THE_CODE = "Show the code in your answer, don't write it to a file or run it."


@dataclass(frozen=True)
class Task:
    id: str
    language: str
    request: str
    hidden: str

    @property
    def prompt(self) -> str:
        return f"{self.request} {SHOW_THE_CODE}"


TASKS = (
    Task("P1", "python", "Write a Python function parse_duration(text: str) -> int that converts strings like '1h30m', '45s', "
                         "'2h5s' or '10m' into a number of seconds. It must raise ValueError for invalid input such as '' or '5x'.",
         "assert parse_duration('1h30m') == 5400\nassert parse_duration('45s') == 45\nassert parse_duration('2h5s') == 7205\n"
         "assert parse_duration('10m') == 600\nfor bad in ('', '5x', 'h'):\n    try:\n        parse_duration(bad)\n"
         "    except ValueError:\n        pass\n    else:\n        raise AssertionError(f'no ValueError for {bad!r}')\n"),
    Task("P2", "python", "Write a Python function merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]] that "
                         "merges overlapping intervals (touching ones count as overlapping) and returns them sorted by start.",
         "assert [tuple(x) for x in merge_intervals([(1, 3), (2, 6), (8, 10), (15, 18)])] == [(1, 6), (8, 10), (15, 18)]\n"
         "assert [tuple(x) for x in merge_intervals([])] == []\nassert [tuple(x) for x in merge_intervals([(1, 4), (4, 5)])] == [(1, 5)]\n"
         "assert [tuple(x) for x in merge_intervals([(5, 6), (1, 2)])] == [(1, 2), (5, 6)]\n"),
    Task("J1", "javascript", "Write a JavaScript function debounce(fn, waitMs) that returns a debounced version of fn: calls made "
                             "within waitMs of each other collapse into one call with the last arguments. Plain Node, no libraries.",
         "let calls = 0;\nlet lastArgument = null;\nconst debounced = debounce((value) => { calls += 1; lastArgument = value; }, 40);\n"
         "debounced(1); debounced(2); debounced(3);\nsetTimeout(() => {\n  if (calls !== 1 || lastArgument !== 3) {\n"
         "    throw new Error(`hidden test failed: calls=${calls} last=${lastArgument}`);\n  }\n}, 150);\n"),
    Task("J2", "javascript", "Write a JavaScript function groupBy(items, keyFn) that returns a plain object mapping each key "
                             "returned by keyFn to an array of the items with that key, keeping their order.",
         "const grouped = groupBy([1, 2, 3, 4, 5], (n) => (n % 2 ? 'odd' : 'even'));\n"
         "if (JSON.stringify(grouped) !== JSON.stringify({ odd: [1, 3, 5], even: [2, 4] }) &&\n"
         "    JSON.stringify(grouped) !== JSON.stringify({ even: [2, 4], odd: [1, 3, 5] })) {\n"
         "  throw new Error('hidden test failed: ' + JSON.stringify(grouped));\n}\n"
         "if (JSON.stringify(groupBy([], (x) => x)) !== '{}') throw new Error('hidden test failed on empty input');\n"),
    Task("T1", "typescript", "Write a TypeScript function chunk<T>(items: T[], size: number): T[][] that splits an array into "
                             "chunks of the given size (the last chunk may be shorter) and throws a RangeError when size is "
                             "less than 1.",
         "const chunked: number[][] = chunk([1, 2, 3, 4, 5], 2);\n"
         "if (JSON.stringify(chunked) !== '[[1,2],[3,4],[5]]') throw new Error('hidden test failed: ' + JSON.stringify(chunked));\n"
         "let threw = false;\ntry { chunk([1], 0); } catch (error) { threw = error instanceof RangeError; }\n"
         "if (!threw) throw new Error('hidden test failed: no RangeError');\n"),
    Task("T2", "typescript", "Write a TypeScript function parseQuery(query: string): Record<string, string | string[]> that parses "
                             "a query string like 'a=1&b=2&a=3'. Values are URL-decoded, and a key that appears more than once "
                             "maps to an array of its values in order.",
         "const parsed = parseQuery('a=1&b=2&a=3&c=hello%20world');\n"
         "const expected = { a: ['1', '3'], b: '2', c: 'hello world' };\n"
         "if (JSON.stringify(parsed) !== JSON.stringify(expected)) throw new Error('hidden test failed: ' + JSON.stringify(parsed));\n"),
    Task("B1", "bash", "Write a bash function join_by that joins its remaining arguments using its first argument as the "
                       "separator, so join_by , a b c prints a,b,c and join_by ' - ' x y prints x - y.",
         "join_by , a b c\necho\njoin_by ' - ' x y\necho\njoin_by , solo\necho\n=a,b,c\nx - y\nsolo\n"),
    Task("B2", "bash", "Write a bash function count_ext that takes a directory and prints how many regular files of each "
                       "extension are directly inside it, one line per extension like 'txt 3', sorted by extension name. "
                       "Files without an extension are skipped.",
         "folder=$(mktemp -d)\ntouch \"$folder/a.txt\" \"$folder/b.txt\" \"$folder/c.md\" \"$folder/README\" \"$folder/d.TXT\" "
         "\"$folder/e.tar.gz\"\nmkdir \"$folder/sub.txt\"\ncount_ext \"$folder\"\n=TXT 1\ngz 1\nmd 1\ntxt 2\n"),
    Task("C1", "c", "Write a C function char *reverse_words(const char *s) that returns a newly allocated string with the order of "
                    "the words reversed, where words are separated by single spaces (\"the sky is blue\" gives \"blue is sky "
                    "the\"). The caller frees the result. Include a main that shows it.",
         "#include <string.h>\n#include <stdlib.h>\n#include <stdio.h>\nint main(void) {\n"
         "    const char *cases[][2] = {{\"the sky is blue\", \"blue is sky the\"}, {\"one\", \"one\"}, {\"\", \"\"}, {\"a b\", \"b a\"}};\n"
         "    for (int i = 0; i < 4; i++) {\n        char *got = reverse_words(cases[i][0]);\n"
         "        if (got == NULL || strcmp(got, cases[i][1]) != 0) {\n"
         "            fprintf(stderr, \"hidden test failed: %s\\n\", got ? got : \"(null)\");\n            abort();\n        }\n"
         "        free(got);\n    }\n    return 0;\n}\n"),
    Task("C2", "c", "Write a C function int is_valid_ipv4(const char *s) that returns 1 if s is a valid dotted IPv4 address (four "
                    "decimal numbers 0-255 separated by dots, no leading zeros, nothing else) and 0 otherwise. Include a main.",
         "#include <stdio.h>\n#include <stdlib.h>\nint main(void) {\n"
         "    const char *good[] = {\"203.0.113.7\", \"0.0.0.0\", \"255.255.255.255\"};\n"
         "    const char *bad[] = {\"256.1.1.1\", \"1.2.3\", \"01.2.3.4\", \"1.2.3.4.5\", \"a.b.c.d\", \"1..2.3\", \"\", \"1.2.3.4 \"};\n"
         "    for (int i = 0; i < 3; i++) if (!is_valid_ipv4(good[i])) { fprintf(stderr, \"hidden test failed: %s\\n\", good[i]); abort(); }\n"
         "    for (int i = 0; i < 8; i++) if (is_valid_ipv4(bad[i])) { fprintf(stderr, \"hidden test failed: %s\\n\", bad[i]); abort(); }\n"
         "    return 0;\n}\n"),
    Task("R1", "rust", "Write a Rust function fn word_frequencies(text: &str) -> std::collections::HashMap<String, usize> that "
                       "counts words case-insensitively, ignoring punctuation (a word is a run of letters, digits or "
                       "apostrophes). Include a main.",
         "fn main() {\n    let counts = word_frequencies(\"The cat; the HAT, the end!\");\n"
         "    assert_eq!(counts.get(\"the\"), Some(&3), \"hidden test failed: {:?}\", counts);\n"
         "    assert_eq!(counts.get(\"cat\"), Some(&1));\n    assert_eq!(counts.get(\"hat\"), Some(&1));\n"
         "    assert_eq!(counts.len(), 4, \"hidden test failed: {:?}\", counts);\n"
         "    assert!(word_frequencies(\"\").is_empty());\n}\n"),
    Task("R2", "rust", "Write a Rust function fn roman_to_int(s: &str) -> Option<u32> that converts a Roman numeral to an "
                       "integer and returns None for empty input or any character that isn't a Roman numeral. Include a main.",
         "fn main() {\n    assert_eq!(roman_to_int(\"MCMXCIV\"), Some(1994));\n    assert_eq!(roman_to_int(\"IX\"), Some(9));\n"
         "    assert_eq!(roman_to_int(\"LVIII\"), Some(58));\n    assert_eq!(roman_to_int(\"ABC\"), None);\n"
         "    assert_eq!(roman_to_int(\"\"), None);\n}\n"),
    Task("G1", "go", "Write a Go function func Reverse(s string) string that reverses a string by Unicode characters (runes), "
                     "with a main that demonstrates it.",
         "func main() {\n\tcases := [][2]string{{\"hello\", \"olleh\"}, {\"héllo\", \"olléh\"}, {\"\", \"\"}, "
         "{\"日本語\", \"語本日\"}}\n\tfor _, c := range cases {\n"
         "\t\tif got := Reverse(c[0]); got != c[1] {\n\t\t\tpanic(\"hidden test failed: \" + got)\n\t\t}\n\t}\n}\n"),
    Task("G2", "go", "Write a Go function func TopK(nums []int, k int) []int that returns the k largest numbers in descending "
                     "order without changing the input slice. If k is larger than len(nums), return all of them. Include a main.",
         "func main() {\n\tinput := []int{5, 1, 9, 3, 7}\n\tgot := TopK(input, 3)\n\twant := []int{9, 7, 5}\n"
         "\tif len(got) != len(want) {\n\t\tpanic(\"hidden test failed: wrong length\")\n\t}\n"
         "\tfor i := range want {\n\t\tif got[i] != want[i] {\n\t\t\tpanic(\"hidden test failed: wrong order\")\n\t\t}\n\t}\n"
         "\tif input[0] != 5 || input[1] != 1 || input[4] != 7 {\n\t\tpanic(\"hidden test failed: input changed\")\n\t}\n"
         "\tif len(TopK([]int{2, 1}, 5)) != 2 {\n\t\tpanic(\"hidden test failed: k too large\")\n\t}\n}\n"),
    Task("V1", "java", "Write a Java class Solution with a static method static boolean isBalanced(String s) that checks whether "
                       "the brackets (), [] and {} in s are balanced; other characters are ignored.",
         "public class Main {\n    public static void main(String[] args) {\n"
         "        String[] good = {\"\", \"()\", \"([]{})\", \"a(b)c[d]\"};\n        String[] bad = {\"(\", \"([)]\", \"}\", \"((())\"};\n"
         "        for (String s : good) if (!Solution.isBalanced(s)) throw new AssertionError(\"hidden test failed: \" + s);\n"
         "        for (String s : bad) if (Solution.isBalanced(s)) throw new AssertionError(\"hidden test failed: \" + s);\n"
         "    }\n}\n"),
    Task("V2", "java", "Write a Java class Solution with a static method static java.util.List<Integer> primesUpTo(int n) that "
                       "returns all primes up to and including n using the Sieve of Eratosthenes (an empty list for n < 2).",
         "public class Main {\n    public static void main(String[] args) {\n"
         "        String got = Solution.primesUpTo(30).toString();\n"
         "        if (!got.equals(\"[2, 3, 5, 7, 11, 13, 17, 19, 23, 29]\")) throw new AssertionError(\"hidden test failed: \" + got);\n"
         "        if (!Solution.primesUpTo(1).isEmpty()) throw new AssertionError(\"hidden test failed for 1\");\n"
         "        if (!Solution.primesUpTo(2).toString().equals(\"[2]\")) throw new AssertionError(\"hidden test failed for 2\");\n"
         "    }\n}\n"),
    Task("S1", "sql", "A SQLite table orders(id INTEGER, customer TEXT, amount REAL, created_at TEXT) already exists. Write one "
                      "query that returns each customer and their total amount as total, only for customers whose total is "
                      "over 100, highest total first.",
         "CREATE TABLE orders (id INTEGER, customer TEXT, amount REAL, created_at TEXT);\n"
         "INSERT INTO orders VALUES (1, 'ann', 80, '2026-01-01'), (2, 'ann', 40, '2026-01-02'), (3, 'bob', 50, '2026-01-03'), "
         "(4, 'cy', 300, '2026-01-04'), (5, 'bob', 20, '2026-01-05');\n"
         "@@expected@@cy|300.0,ann|120.0"),
    Task("S2", "sql", "A SQLite table employees(id INTEGER, name TEXT, manager_id INTEGER) already exists. Write one query that "
                      "lists every employee's name and their manager's name (NULL for anyone without a manager), ordered by "
                      "employee name.",
         "CREATE TABLE employees (id INTEGER, name TEXT, manager_id INTEGER);\n"
         "INSERT INTO employees VALUES (1, 'Zoe', NULL), (2, 'Adam', 1), (3, 'Beth', 1), (4, 'Carl', 2);\n"
         "@@expected@@Adam|Zoe,Beth|Zoe,Carl|Adam,Zoe|-"),
)


def code_for(language: str, answer: str) -> str:
    blocks = [body for _, tag, body in FENCE.findall(answer or "") if tag.lower() in TAGS[language]]
    return "\n".join(blocks)


def renamed_main(code: str, pattern: str, replacement: str) -> str:
    return re.sub(pattern, replacement, code, flags=re.M)


def bash_program(code: str, hidden: str) -> str:
    calls, _, expected = hidden.partition("\n=")
    script = f"{code}\necho '{MARKER}'\n{calls}\n"
    return ("import subprocess\n"
            f"script = {script!r}\n"
            "finished = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=8)\n"
            f"shown = finished.stdout.split({MARKER!r} + '\\n', 1)[-1]\n"
            f"expected = {expected!r}\n"
            "assert shown == expected, f'hidden test failed: {shown!r} (stderr {finished.stderr[-300:]!r})'\n")


def sql_program(code: str, hidden: str) -> str:
    setup, _, expected = hidden.partition("@@expected@@")
    statements = [part.strip() for part in re.split(r";\s*(?:\n|$)", code) if part.strip()]
    queries = [part for part in statements if re.match(r"(?is)^\s*(?:with|select)\b", part)]
    query = queries[-1] if queries else "SELECT NULL WHERE 0"
    return ("import sqlite3\n"
            "connection = sqlite3.connect(':memory:')\n"
            f"connection.executescript({setup!r})\n"
            f"rows = connection.execute({query!r}).fetchall()\n"
            "shown = ','.join('|'.join('-' if value is None else str(value) for value in row) for row in rows)\n"
            f"expected = {expected!r}\n"
            "assert shown == expected, f'hidden test failed: {shown!r}'\n")


def program_for(task: Task, answer: str) -> tuple[str, str]:
    code = code_for(task.language, answer)
    if not code.strip():
        return task.language, ""
    if task.language == "python":
        return "python", f"{code}\n\n{task.hidden}"
    if task.language in {"javascript", "typescript"}:
        return task.language, f"{code}\n\n{task.hidden}"
    if task.language == "bash":
        return "python", bash_program(code, task.hidden)
    if task.language == "c":
        return "c", renamed_main(code, r"\bint\s+main\s*\(", "int shown_main(") + "\n" + task.hidden
    if task.language == "rust":
        return "rust", "#![allow(dead_code)]\n" + renamed_main(code, r"\bfn\s+main\s*\(", "fn shown_main(") + "\n" + task.hidden
    if task.language == "go":
        code = renamed_main(code, r"^\s*func\s+main\s*\(\s*\)", "func shownMain()")
        code = re.sub(r"^\s*package\s+\w+", "package main", code, count=1, flags=re.M)
        if not re.search(r"^\s*package\s", code, re.M):
            code = "package main\n\n" + code
        return "go", code + "\n" + task.hidden
    if task.language == "java":
        code = re.sub(r"^(\s*)public\s+(?=(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\b)", r"\1", code, flags=re.M)
        return "java", code + "\n" + task.hidden
    if task.language == "sql":
        return "python", sql_program(code, task.hidden)
    raise ValueError(f"no scorer for {task.language}")


def checked(runner_url: str, language: str, code: str) -> dict:
    body = json.dumps({"language": language, "code": code}).encode()
    request = urllib.request.Request(f"{runner_url.rstrip('/')}/check", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def score(runner_url: str, task: Task, answer: str) -> dict:
    language, program = program_for(task, answer)
    if not program:
        return {"passed": False, "reason": "no code block in the task's language"}
    verdict = checked(runner_url, language, program)
    ran = bool({"run", "tests"} & set(verdict.get("checked") or []))
    passed = verdict["status"] == "passed" and ran
    reason = "" if passed else (verdict.get("problem") or "; ".join(verdict.get("notes") or []) or verdict["status"])[:600]
    return {"passed": passed, "reason": reason}


if __name__ == "__main__":
    runner, results_file = sys.argv[1:3]
    results = json.load(open(results_file))
    by_id = {task.id: task for task in TASKS}
    for result in results:
        if result["id"] in by_id:
            print(result["id"], score(runner, by_id[result["id"]], result["answer"]))
