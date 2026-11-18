import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import languages  # noqa: E402
import runner  # noqa: E402

CHECKS = str(Path(__file__).resolve().parents[1] / "checks.py")
C_FLAGS = ("-std=gnu17", "-fsanitize=address,undefined", "-fno-sanitize-recover=all")
PASSED_STEP = runner.StepResult("success", 0, "", 0.1)

NAME_ERROR_FIRST = "import sys\nprint(totl)\nname = input()\nprint(name)\n"
TYPE_ERROR_FIRST = ("import sys\n\ndef area(width, height):\n    return width * height\n\nprint(area(3))\n"
                    "count = int(sys.argv[1])\nprint(count)\n")
OPTIONAL_ONLY_BUG = ("import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--count', type=int, default=3)\n"
                     "options = parser.parse_args()\nitems = [1, 2, 3]\nprint(items[options.count])\n")
OPTIONAL_ONLY_FINE = OPTIONAL_ONLY_BUG.replace("items[options.count]", "items[options.count - 1]")
FAILING_TESTS = ("import sys\n\ndef add(a, b):\n    return a - b\n\ndef show():\n    print(sys.argv[1])\n\n"
                 "def test_add():\n    assert add(1, 2) == 3\n")
DEEP_RECURSION = "def depth(n):\n    return depth(n + 1)\n\nprint(depth(0))\nname = input()\n"
UNUSED_READER = "def ask():\n    return input('n? ')\n\nvalues = [1, 2, 3]\nprint(values[3])\n"
USAGE_EXIT = "import sys\nif len(sys.argv) < 2:\n    print('usage: main.py N')\n    sys.exit(2)\nprint(int(sys.argv[1]) ** 2)\n"
JS_REFERENCE_FIRST = "const total = cuont + 1;\nconsole.log(total, process.argv[2]);\n"
C_OVERFLOW_FIRST = ("#include <stdio.h>\nint main(void) {\n    int items[3] = {1, 2, 3};\n    int index = 3;\n    items[index] = 7;\n"
                    "    int n = 0;\n    scanf(\"%d\", &n);\n    printf(\"%d %d\\n\", n, items[0]);\n    return 0;\n}\n")
SQUARE_FROM_ARGV = "import sys\nnumber = int(sys.argv[1])\nprint(number * number)\n"
SQUARE_FROM_STDIN = "number = int(input())\nprint(number * number)\n"
STDIN_REAL_BUG = "number = int(input())\nitems = [1, 2, 3]\nprint(items[number + 3])\n"
STDIN_THEN_FILE = "number = int(input())\nprint(open('data.txt').read(), number)\n"
DEFAULT_PATH_BUG = "import sys\ncount = int(sys.argv[1]) if len(sys.argv) > 1 else None\nprint(count + 1)\n"
RERUN_FINDS_NAME_ERROR = "import sys\nvalues = [int(word) for word in sys.stdin.read().split()]\nprint(max(values))\nprint(totl)\n"
ARGPARSE_TYPO = ("import argparse\nparser = argparse.ArgumentParser(descripton='adds numbers')\n"
                 "parser.add_argument('numbers', nargs='*', type=int)\nprint(sum(parser.parse_args().numbers))\n")
JS_TYPO_ON_THE_READ_LINE = "const shown = Number(process.argv[2] || 3).toFixd(2);\nconsole.log(shown);\n"
COMMENT_NAMES_INPUT = "values = [1, 2, 3]\nprint(values[3])  # the caller passes input() later\nname = input()\n"
AVERAGE_OF_STDIN = "import sys\nnumbers = [float(word) for word in sys.stdin.read().split()]\nprint(sum(numbers) / len(numbers))\n"
TWO_ARGUMENTS_RAISE = ("import sys\n\ndef main():\n    numbers = sys.argv[1:]\n    if len(numbers) != 2:\n"
                       "        raise ValueError('give two numbers')\n    print(int(numbers[0]) + int(numbers[1]))\n\nmain()\n")
TWO_ARGUMENTS_INDEX = "import sys\nvalues = list(map(int, sys.argv[1:]))\nprint(values[0] * values[1])\n"
JS_TWO_ARGUMENTS_THROW = ("const [first, second] = process.argv.slice(2).map(Number);\nif (Number.isNaN(first + second)) {\n"
                          "  throw new Error('give two numbers');\n}\nconsole.log(first + second);\n")
CSV_ON_STDIN = "import sys\nlines = sys.stdin.read().split()\nrows = [line.split(',') for line in lines] or [[]]\nprint(rows[0][1])\n"
TEN_NUMBERS = ("import sys\nnumbers = [int(word) for word in sys.stdin.read().split()]\nif len(numbers) < 10:\n"
               "    raise ValueError('need at least 10 numbers')\nprint(sorted(numbers)[:10])\n")
C_ASSERT_TWO_ARGUMENTS = ("#include <assert.h>\n#include <stdio.h>\n#include <stdlib.h>\nint main(int argc, char **argv) {\n"
                          "    assert(argc == 3);\n    printf(\"%d\\n\", atoi(argv[1]) + atoi(argv[2]));\n    return 0;\n}\n")
TITLE_TYPO = "name = input('Name: ').strip().titel()\nprint('Hello ' + name)\n"
SUM_OF_STRINGS = "total = sum(input().split())\nprint(total)\n"
UPPER_TYPO = "import sys\nprint([word.uper() for word in sys.stdin.read().split()])\n"

REAL_CRASHES_WITHOUT_INPUT = (
    ("python", "NameError before anything is read", NAME_ERROR_FIRST),
    ("python", "TypeError in a helper before argv is read", TYPE_ERROR_FIRST),
    ("python", "argparse with optional arguments only, off by one", OPTIONAL_ONLY_BUG),
    ("python", "its own test fails, another function names sys.argv", FAILING_TESTS),
    ("python", "endless recursion before input()", DEEP_RECURSION),
    ("python", "input() only in a function that is never called", UNUSED_READER),
    ("javascript", "ReferenceError before process.argv is used", JS_REFERENCE_FIRST),
    ("c", "write past an array before scanf", C_OVERFLOW_FIRST),
    ("python", "the no-argument default path crashes", DEFAULT_PATH_BUG),
    ("python", "made-up input gets past the read and then hits a NameError", RERUN_FINDS_NAME_ERROR),
    ("python", "misspelt keyword on the argparse line", ARGPARSE_TYPO),
    ("javascript", "misspelt method on the process.argv line", JS_TYPO_ON_THE_READ_LINE),
    ("python", "the crash line names input() only in a comment", COMMENT_NAMES_INPUT),
    ("php", "undefined function on the $argv line", "<?php\n$name = strtouper($argv[1] ?? 'bob');\necho $name, \"\\n\";\n"),
    ("php", "undefined function after the argument is read",
     "<?php\n$name = $argv[1] ?? 'bob';\necho strtouper($name), \"\\n\";\n"),
)
CORRECT_PROGRAMS_WANTING_INPUT = (
    ("python", "argument read on the crash line", SQUARE_FROM_ARGV),
    ("python", "input() at the end of stdin", SQUARE_FROM_STDIN),
    ("python", "usage message and exit", USAGE_EXIT),
    ("python", "average of empty stdin", AVERAGE_OF_STDIN),
    ("python", "wants two arguments, raises without", TWO_ARGUMENTS_RAISE),
    ("python", "wants two arguments, indexes a list", TWO_ARGUMENTS_INDEX),
    ("javascript", "wants two arguments, throws without", JS_TWO_ARGUMENTS_THROW),
    ("python", "wants comma separated rows on stdin", CSV_ON_STDIN),
    ("python", "wants ten numbers on stdin", TEN_NUMBERS),
    ("c", "asserts two arguments", C_ASSERT_TWO_ARGUMENTS),
)
PROGRAMS_THAT_PARSE_WHAT_THEY_READ = (
    ("python", "square of argv[1]", "import sys\nnumber = int(sys.argv[1])\nprint(number * number)\n"),
    ("python", "square of input()", "number = int(input())\nprint(number * number)\n"),
    ("python", "two arguments, raises without", "import sys\n\ndef main():\n    numbers = sys.argv[1:]\n    if len(numbers) != 2:\n"
     "        raise ValueError('give two numbers')\n    print(int(numbers[0]) + int(numbers[1]))\n\nmain()\n"),
    ("python", "ten numbers on stdin", "import sys\nnumbers = [int(word) for word in sys.stdin.read().split()]\n"
     "if len(numbers) < 10:\n    raise ValueError('need at least 10 numbers')\nprint(sorted(numbers)[:10])\n"),
    ("python", "one number from sys.stdin.read(), parsed on the next line",
     "import sys\ntext = sys.stdin.read().strip()\nnumber = int(text)\nprint(number * 2)\n"),
    ("python", "JSON from sys.stdin.read(), parsed on the next line",
     "import json\nimport sys\ntext = sys.stdin.read()\ndata = json.loads(text)\nfor key in sorted(data):\n    print(key, data[key])\n"),
    ("python", "JSON object straight from json.load(sys.stdin)",
     "import json\nimport sys\ndata = json.load(sys.stdin)\nfor key in sorted(data):\n    print(key, data[key])\n"),
    ("python", "three numbers on stdin, unpacked on the next line",
     "import sys\nparts = sys.stdin.read().split()\na, b, c = map(int, parts)\nprint(a + b + c)\n"),
    ("python", "a date on stdin, parsed on the next line",
     "import sys\nfrom datetime import datetime\nline = sys.stdin.readline().strip()\nday = datetime.strptime(line, '%Y-%m-%d')\n"
     "print(day.strftime('%A'))\n"),
    ("python", "CSV with a name column on stdin",
     "import csv\nimport sys\nrows = list(csv.reader(sys.stdin))\nheader = rows[0]\ncolumn = header.index('name')\n"
     "for row in rows[1:]:\n    print(row[column])\n"),
    ("python", "a command word on stdin, unknown ones raise with the word",
     "import sys\ncommand = sys.stdin.readline().strip()\nif command not in ('start', 'stop'):\n"
     "    raise ValueError(f'unknown command {command!r}')\nprint(command, 'done')\n"),
    ("python", "four arguments, raises without", "import sys\narguments = sys.argv[1:]\nif len(arguments) != 4:\n"
     "    raise ValueError('give four numbers')\nprint(sum(map(int, arguments)))\n"),
    ("python", "a.b.c.d address in argv, validated on the next line",
     "import sys\naddress = sys.argv[1] if len(sys.argv) > 1 else ''\nparts = address.split('.')\nif len(parts) != 4:\n"
     "    raise ValueError(f'not an IPv4 address: {address!r}')\nprint([int(part) for part in parts])\n"),
    ("python", "key=value pairs on stdin", "import sys\nlines = sys.stdin.read().splitlines()\nif not lines:\n"
     "    raise ValueError('no settings given')\nsettings = dict(line.split('=', 1) for line in lines)\nprint(settings)\n"),
    ("python", "a matrix on stdin: first line is the size",
     "import sys\nlines = sys.stdin.read().splitlines()\nsize = int(lines[0])\n"
     "rows = [list(map(int, line.split())) for line in lines[1:size + 1]]\nprint(sum(rows[index][index] for index in range(size)))\n"),
    ("python", "a literal on stdin read with ast.literal_eval",
     "import ast\nimport sys\nvalue = ast.literal_eval(sys.stdin.read())\nprint(type(value).__name__)\n"),
    ("python", "re.match on stdin text", "import re\nimport sys\ntext = sys.stdin.read()\nfound = re.match(r'(\\d+)-(\\d+)', text)\n"
     "print(int(found.group(1)) + int(found.group(2)))\n"),
    ("javascript", "two arguments, throws without",
     "const [first, second] = process.argv.slice(2).map(Number);\nif (Number.isNaN(first + second)) {\n"
     "  throw new Error('give two numbers');\n}\nconsole.log(first + second);\n"),
    ("javascript", "JSON from process.stdin", "let text = '';\nprocess.stdin.on('data', (chunk) => { text += chunk; });\n"
     "process.stdin.on('end', () => {\n  const data = JSON.parse(text);\n  console.log(Object.keys(data));\n});\n"),
    ("javascript", "JSON from process.argv[2]", "const data = JSON.parse(process.argv[2]);\nconsole.log(Object.keys(data));\n"),
    ("javascript", "a name argument, upper-cased on the next line", "const name = process.argv[2];\nconsole.log(name.toUpperCase());\n"),
    ("javascript", "a word from stdin lines, split on the next line",
     "const lines = require('fs').readFileSync(process.stdin.fd, 'utf8').split('\\n');\n"
     "const [key, value] = lines[0].split('=');\nconsole.log(key.trim(), value.trim());\n"),
    ("c", "asserts two arguments", "#include <assert.h>\n#include <stdio.h>\n#include <stdlib.h>\nint main(int argc, char **argv) {\n"
     "    assert(argc == 3);\n    printf(\"%d\\n\", atoi(argv[1]) + atoi(argv[2]));\n    return 0;\n}\n"),
    ("php", "an argument, used on the same line", "<?php\n$number = intdiv(100, (int) $argv[1]);\necho $number, \"\\n\";\n"),
)
LIBRARY_CRASHES_ON_EVERY_RUN = (
    ("python", "dataclass with a mutable default, the script reads an argument",
     "import sys\nfrom dataclasses import dataclass\n\n\n@dataclass\nclass Basket:\n    owner: str\n    items: list = []\n\n\n"
     "basket = Basket(sys.argv[1])\nprint(basket)\n"),
    ("python", "dataclass with a mutable default, the script uses input()",
     "from dataclasses import dataclass\n\n\n@dataclass\nclass Basket:\n    owner: str\n    items: list = []\n\n\n"
     "basket = Basket(input('Owner: '))\nprint(basket)\n"),
    ("python", "json.dumps of a set, the script reads an argument",
     "import json\nimport sys\nname = sys.argv[1] if len(sys.argv) > 1 else 'report'\ntags = {'a', 'b'}\n"
     "print(json.dumps({'name': name, 'tags': tags}))\n"),
    ("python", "json.dumps of a datetime, numbers read from stdin",
     "import json\nimport sys\nfrom datetime import datetime\nnumbers = [int(word) for word in sys.stdin.read().split()]\n"
     "print(json.dumps({'when': datetime.now(), 'total': sum(numbers)}))\n"),
    ("python", "strptime with a format that never fits its constant, an argument read later",
     "import sys\nfrom datetime import datetime\nstart = datetime.strptime('2024-05-01', '%d/%m/%Y')\nprint(start, sys.argv[1:])\n"),
    ("python", "a bad constant regular expression, text read from stdin",
     "import re\nimport sys\ntext = sys.stdin.read()\nfound = re.findall(r'(\\d+', text)\nprint(found)\n"),
    ("python", "random.sample larger than its constant list, an argument read",
     "import random\nimport sys\nseed = int(sys.argv[1]) if len(sys.argv) > 1 else 0\nrandom.seed(seed)\n"
     "print(random.sample([1, 2, 3], 5))\n"),
    ("python", "statistics.mean of an empty constant list, an argument read",
     "import statistics\nimport sys\nlabel = sys.argv[1] if len(sys.argv) > 1 else 'mean'\nprint(label, statistics.mean([]))\n"),
    ("python", "string.Template with a missing key, a name read from stdin",
     "import sys\nfrom string import Template\nname = sys.stdin.readline().strip() or 'you'\n"
     "print(Template('Hello $name, you owe $amount').substitute(name=name))\n"),
    ("python", "os.path.join with a number, an argument read",
     "import os\nimport sys\nfolder = sys.argv[1] if len(sys.argv) > 1 else '.'\nprint(os.path.join(folder, 2024))\n"),
    ("python", "the same json.dumps bug in a script that reads nothing",
     "import json\ntags = {'a', 'b'}\nprint(json.dumps({'tags': tags}))\n"),
    ("javascript", "JSON.parse of a constant that is not JSON, an argument read",
     "const name = process.argv[2] || 'x';\nconst settings = JSON.parse(\"{'size': 3}\");\nconsole.log(name, settings.size);\n"),
    ("javascript", "a wrong argument type inside a node library call, an argument read",
     "const path = require('path');\nconst name = process.argv[2] || 'x';\nconsole.log(path.join(name, 2024));\n"),
    ("javascript", "the same JSON.parse bug in a script that reads nothing",
     "const settings = JSON.parse(\"{'size': 3}\");\nconsole.log(settings.size);\n"),
)

MADE_UP_INPUT_OF_ANOTHER_TYPE = (
    ("python", "one JSON object per line, a key is printed",
     "import json\nimport sys\nline = sys.stdin.readline()\nrecord = json.loads(line)\nprint(record['name'])\n"),
    ("python", "one JSON object per line, record.get on the next line",
     "import json\nimport sys\nline = sys.stdin.readline()\nrecord = json.loads(line)\nprint(record.get('name', 'nobody'))\n"),
    ("python", "a JSON list on one line, items joined",
     "import json\nimport sys\nline = sys.stdin.readline()\nitems = json.loads(line)\nprint(', '.join(items))\n"),
    ("python", "a literal list on stdin with ast, summed on a later line",
     "import ast\nimport sys\ntext = sys.stdin.readline()\nvalues = ast.literal_eval(text)\nprint(sum(values))\n"),
    ("javascript", "a JSON list in argv, parsed on the next line",
     "const raw = process.argv[2];\nconst items = JSON.parse(raw);\nitems.forEach((item) => console.log(item));\n"),
    ("javascript", "a JSON list in argv, forEach on a later line",
     "const raw = process.argv[2] || '';\nconst items = JSON.parse(raw || 'null');\nitems.forEach((item) => console.log(item));\n"),
    ("javascript", "a handler looked up by the first argument",
     "const name = process.argv[2];\nconst text = name.trim();\n"
     "const handlers = { hello: () => console.log('hi'), bye: () => console.log('bye') };\nhandlers[text]();\n"),
)

READS_FROM_DESCRIPTOR_ZERO = (
    ("python", "competitive style: open(0)", "count, *values = map(int, open(0).read().split())\nprint(sum(values[:count]))\n"),
    ("javascript", "JSON from readFileSync(0)",
     "const fs = require('fs');\nconst data = JSON.parse(fs.readFileSync(0, 'utf8'));\nconsole.log(Object.keys(data).length);\n"),
    ("javascript", "lines from /dev/stdin",
     "const fs = require('fs');\nconst [first] = fs.readFileSync('/dev/stdin', 'utf8').split('\\n');\n"
     "console.log(JSON.parse(first).length);\n"),
)

NUMBERS_FROM_STDIN = "import sys\nnumbers = [int(word) for word in sys.stdin.read().split()]\naverage = sum(numbers) / len(numbers)\n"
COUNT_FROM_ARGUMENTS = "import sys\nvalues = sys.argv[1:]\ncount = int(values[0])\n"
JS_SENTENCE_FROM_ARGUMENTS = "const given = process.argv.slice(2);\nconst sentence = given[0];\nconst words = sentence.split(' ');\n"
JS_NUMBERS_FROM_STDIN = ("const fs = require('fs');\nconst text = fs.readFileSync(process.stdin.fd, 'utf8');\n"
                         "const [label, ...rest] = text.trim().split('\\n');\nconst second = rest[0].trim();\n"
                         "const numbers = rest.map(Number);\n")
REAL_TYPE_BUGS_AFTER_MADE_UP_INPUT = (
    ("python", "a for loop over an int", NUMBERS_FROM_STDIN + "half = len(numbers) // 2\nfor index in half:\n    print(numbers[index])\n"),
    ("python", "range of a float", NUMBERS_FROM_STDIN + "for index in range(len(numbers) / 2):\n    print(numbers[index])\n"),
    ("python", "max shadowed by its own result, then called", NUMBERS_FROM_STDIN + "max = max(numbers)\nprint(max(numbers[:3]))\n"),
    ("python", "a string method on a float", NUMBERS_FROM_STDIN + "print('average:', average.rjust(10))\n"),
    ("python", "append on a counter that is an int", NUMBERS_FROM_STDIN + "seen = 0\nfor number in numbers:\n    seen.append(number)\n"),
    ("python", "indexing an int", NUMBERS_FROM_STDIN + "largest = max(numbers)\nprint('first digit', largest[0])\n"),
    ("python", "join of a count", NUMBERS_FROM_STDIN + "print(', '.join(len(numbers)))\n"),
    ("python", "append on a bool", NUMBERS_FROM_STDIN + "found = any(number > 10 for number in numbers)\nfound.append(average)\n"),
    ("python", "a for loop over the int from int()", COUNT_FROM_ARGUMENTS + "for index in count:\n    print(index)\n"),
    ("python", "isdigit after int()", COUNT_FROM_ARGUMENTS + "if not count.isdigit():\n    print('not a number')\n"),
    ("python", "indexing the int from int()", COUNT_FROM_ARGUMENTS + "print('first digit:', count[0])\n"),
    ("python", "len shadowed by a number, then called", COUNT_FROM_ARGUMENTS + "len = count * 2\nprint(len(values))\n"),
    ("javascript", "words.contains", JS_SENTENCE_FROM_ARGUMENTS + "console.log(words.contains('a') ? 'yes' : 'no');\n"),
    ("javascript", "reverse on a string", JS_SENTENCE_FROM_ARGUMENTS + "console.log(sentence.reverse(), words.length);\n"),
    ("javascript", "length called as a function", JS_SENTENCE_FROM_ARGUMENTS + "console.log(words.length());\n"),
    ("javascript", "words.first", JS_SENTENCE_FROM_ARGUMENTS + "console.log(words.first().toUpperCase());\n"),
    ("javascript", "for of over a number", JS_SENTENCE_FROM_ARGUMENTS + "for (const index of words.length) { console.log(index); }\n"),
    ("javascript", "Math.sum", JS_SENTENCE_FROM_ARGUMENTS + "console.log(Math.sum(words.length, 1));\n"),
    ("javascript", "numbers.sum on stdin numbers", JS_NUMBERS_FROM_STDIN + "console.log(numbers.sum() / numbers.length);\n"),
    ("javascript", "numbers.max on stdin numbers", JS_NUMBERS_FROM_STDIN + "console.log(numbers.max());\n"),
    ("javascript", "spread of a number", JS_NUMBERS_FROM_STDIN + "console.log([...numbers.length]);\n"),
)

CODE_ERROR_ONLY_A_LATER_SHAPE_REACHES = (
    ("python", "NameError reached with two arguments",
     "import sys\nvalues = sys.argv[1:]\nwidth, height = values[0], values[1]\nprint(int(width) * int(heigth))\n", "NameError"),
    ("python", "NameError reached on the comma line",
     "import sys\ntext = sys.stdin.readline().strip()\nfirst, second = text.split(',')\nprint(int(first) + int(secnd))\n", "NameError"),
    ("python", "undefined function reached with two arguments",
     "import sys\nvalues = sys.argv[1:]\nsource, target = values[0], values[1]\nprint(joined(source, target))\n", "NameError"),
    ("javascript", "ReferenceError reached with two arguments",
     "const given = process.argv.slice(2);\nconst left = given[0];\nconst right = given[1];\nconst both = left.trim() + right.trim();\n"
     "console.log(both, totl);\n", "ReferenceError"),
    ("python", "str plus int reached with three arguments",
     "import sys\nvalues = sys.argv[1:]\nname, start, end = values[0], values[1], values[2]\nprint(name + ': ' + (int(end) - int(start)))\n",
     "can only concatenate"),
)

CORRECT_WHEN_THE_LAST_SHAPE_RUNS_TO_THE_END = (
    ("python", "eval of the words after the mode",
     "import sys\nmode = sys.argv[1] if len(sys.argv) > 1 else 'calc'\nexpression = ' '.join(sys.argv[2:])\nresult = eval(expression)\n"
     "print(mode, result)\n"),
    ("python", "ast.literal_eval of the words after the first argument",
     "import ast\nimport sys\nlabel = sys.argv[1] if len(sys.argv) > 1 else 'value'\ntext = ' '.join(sys.argv[2:])\n"
     "value = ast.literal_eval(text)\nprint(label, value)\n"),
    ("javascript", "JSON.parse of the words after the first argument",
     "const label = process.argv[2] || 'value';\nconst text = process.argv.slice(3).join(' ');\nconst value = JSON.parse(text);\n"
     "console.log(label, value);\n"),
    ("python", "Path of the second argument, None when missing",
     "import sys\nfrom pathlib import Path\nmode = sys.argv[1] if len(sys.argv) > 1 else 'show'\n"
     "target = sys.argv[2] if len(sys.argv) > 2 else None\npath = Path(target)\nprint(mode, path.name)\n"),
    ("python", "os.path.join of the second argument, None when missing",
     "import os\nimport sys\nbase = sys.argv[1] if len(sys.argv) > 1 else '.'\nname = sys.argv[2] if len(sys.argv) > 2 else None\n"
     "print(os.path.join(base, name))\n"),
    ("python", "re.compile of the second argument, None when missing",
     "import re\nimport sys\ntext = sys.argv[1] if len(sys.argv) > 1 else ''\npattern = sys.argv[2] if len(sys.argv) > 2 else None\n"
     "compiled = re.compile(pattern)\nprint(bool(compiled.search(text)))\n"),
)

TYPO_SUGGESTED_AFTER_MADE_UP_INPUT = ("import sys\nvalues = [int(word) for word in sys.stdin.read().split()]\nlargest = max(values)\n"
                                      "print(largest.bit_lenght())\n")

GIVEN_INPUT = (
    ("python", "stdin given, correct program reads sys.argv[1]", SQUARE_FROM_ARGV, "5\n", (), runner.NEEDS_INPUT),
    ("python", "argument given, correct program reads input()", SQUARE_FROM_STDIN, "", ("5",), runner.NEEDS_INPUT),
    ("python", "stdin given, correct program reads input()", SQUARE_FROM_STDIN, "5\n", (), "passed"),
    ("python", "argument given, correct program reads argv", SQUARE_FROM_ARGV, "", ("5",), "passed"),
    ("python", "stdin given, real bug after the read", STDIN_REAL_BUG, "5\n", (), "failed"),
    ("python", "stdin '3 4)' from the question, the read fails on it", SQUARE_FROM_STDIN, "3 4)\n", (), "failed"),
    ("python", "stdin '-n 5' from the question, the read fails on it", SQUARE_FROM_STDIN, "-n 5\n", (), "failed"),
    ("python", "stdin given, then a file the sandbox lacks", STDIN_THEN_FILE, "5\n", (), runner.NEEDS_INPUT),
    ("python", "stdin 'bob', misspelt method on the input() line", TITLE_TYPO, "bob\n", (), "failed"),
    ("python", "stdin '3 4', sum of strings on the input() line", SUM_OF_STRINGS, "3 4\n", (), "failed"),
    ("javascript", "argument 5, misspelt method on the argv line", JS_TYPO_ON_THE_READ_LINE, "", ("5",), "failed"),
    ("python", "stdin 'a b', misspelt method on the stdin line", UPPER_TYPO, "a b\n", (), "failed"),
)


INTERPRETERS = {"javascript": "node", "c": "gcc", "php": "php"}


def can_run(language: str) -> bool:
    return bool(shutil.which(INTERPRETERS.get(language, "python3")))


class RealPrograms(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def ran(self, command: list[str], stdin: str) -> runner.StepResult:
        done = subprocess.run(command, cwd=self.folder, input=stdin, capture_output=True, text=True, timeout=60)
        result = "success" if done.returncode == 0 else ("signal" if done.returncode < 0 else "exit-code")
        return runner.StepResult(result, done.returncode, done.stdout + done.stderr, 0.1, done.stdout)

    def program_run(self, language: str, code: str, step_name: str, given: runner.RunInput) -> runner.StepResult:
        if language == "python":
            (self.folder / "main.py").write_text(code)
            if step_name == languages.TESTS:
                return self.ran([sys.executable, "-I", CHECKS, "pytests", "main.py"], given.stdin)
            return self.ran([sys.executable, "-I", "main.py", *given.arguments], given.stdin)
        if language == "javascript":
            (self.folder / "main.js").write_text(code)
            return self.ran([shutil.which("node"), "main.js", *given.arguments], given.stdin)
        if language == "php":
            (self.folder / "main.php").write_text(code)
            return self.ran([shutil.which("php"), "main.php", *given.arguments], given.stdin)
        (self.folder / "main.c").write_text(code)
        subprocess.run(["gcc", *C_FLAGS, "-o", "main", "main.c"], cwd=self.folder, check=True, capture_output=True, timeout=120)
        return self.ran(["./main", *given.arguments], given.stdin)

    def checked(self, language: str, code: str, stdin: str = "", arguments: tuple = ()) -> tuple[dict, int]:
        runs = []

        def run_step(run, step, plan, go_cache, given):
            if step.name not in runner.RUN_STEPS:
                return PASSED_STEP
            runs.append(given)
            return self.program_run(language, code, step.name, given)

        with unittest.mock.patch.object(runner, "prepared_run", return_value=self.folder), \
                unittest.mock.patch.object(runner, "removed_run"), \
                unittest.mock.patch.object(runner, "run_step", side_effect=run_step):
            verdict = runner.checked_code(language, code, "", True, runner.RunInput(stdin, tuple(arguments)))
        return verdict, len(runs)

    def test_a_real_crash_without_input_fails_also_after_the_reruns(self):
        for language, label, code in REAL_CRASHES_WITHOUT_INPUT:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual("failed", verdict["status"], verdict)

    def test_a_correct_program_that_wants_more_input_is_never_failed(self):
        for language, label, code in CORRECT_PROGRAMS_WANTING_INPUT:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], verdict)

    def test_a_correct_program_that_parses_what_it_reads_is_never_failed(self):
        for language, label, code in PROGRAMS_THAT_PARSE_WHAT_THEY_READ:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertIn(verdict["status"], ("passed", runner.NEEDS_INPUT), verdict)

    def test_a_parse_error_on_input_is_not_a_code_error(self):
        for language, code in (("python", "import ast\nimport sys\nprint(ast.literal_eval(sys.stdin.read()))\n"),
                               ("javascript", "const data = JSON.parse(process.argv[2]);\nconsole.log(data);\n")):
            if not can_run(language):
                continue
            with self.subTest(language=language):
                verdict, _ = self.checked(language, code)
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], verdict)

    def test_a_crash_a_library_raises_the_same_way_on_every_run_fails(self):
        for language, label, code in LIBRARY_CRASHES_ON_EVERY_RUN:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual("failed", verdict["status"], verdict)

    def test_a_crash_that_repeats_shows_its_own_error_not_a_later_made_up_one(self):
        code = next(code for _, label, code in LIBRARY_CRASHES_ON_EVERY_RUN if label.startswith("json.dumps of a datetime"))
        verdict, _ = self.checked("python", code)
        self.assertIn("is not JSON serializable", verdict["problem"])
        self.assertNotIn("made-up input", verdict["problem"])

    def test_made_up_input_that_parses_to_another_type_is_not_a_code_error(self):
        for language, label, code in MADE_UP_INPUT_OF_ANOTHER_TYPE:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], verdict)

    def test_a_type_bug_the_program_made_itself_fails_after_made_up_input(self):
        for language, label, code in REAL_TYPE_BUGS_AFTER_MADE_UP_INPUT:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual("failed", verdict["status"], verdict)

    def test_a_code_error_only_a_later_shape_reaches_is_the_error_shown(self):
        for language, label, code, deciding_error in CODE_ERROR_ONLY_A_LATER_SHAPE_REACHES:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual("failed", verdict["status"], verdict)
                self.assertIn(deciding_error, verdict["problem"])

    def test_a_correct_program_the_last_shape_runs_to_the_end_is_never_failed(self):
        for language, label, code in CORRECT_WHEN_THE_LAST_SHAPE_RUNS_TO_THE_END:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], verdict)

    def test_reading_descriptor_zero_or_dev_stdin_counts_as_reading_input(self):
        for language, label, code in READS_FROM_DESCRIPTOR_ZERO:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code)
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], verdict)

    def test_a_typo_python_suggests_a_fix_for_still_fails_after_made_up_input(self):
        verdict, _ = self.checked("python", TYPO_SUGGESTED_AFTER_MADE_UP_INPUT)
        self.assertEqual("failed", verdict["status"], verdict)
        self.assertIn("bit_lenght", verdict["problem"])

    def test_a_correct_optional_only_program_passes(self):
        self.assertEqual("passed", self.checked("python", OPTIONAL_ONLY_FINE)[0]["status"])

    def test_a_failure_found_by_made_up_input_shows_that_error(self):
        verdict, runs = self.checked("python", RERUN_FINDS_NAME_ERROR)
        self.assertIn("NameError", verdict["problem"])
        self.assertEqual(2, runs)

    def test_a_crash_no_input_causes_is_not_rerun(self):
        for language, code in (("python", ARGPARSE_TYPO), ("python", DEFAULT_PATH_BUG), ("javascript", JS_TYPO_ON_THE_READ_LINE)):
            if not can_run(language):
                continue
            with self.subTest(code=code[:40]):
                self.assertEqual(1, self.checked(language, code)[1])

    def test_input_from_the_question_fails_a_program_it_reached_and_crashed(self):
        for language, label, code, stdin, arguments, wanted in GIVEN_INPUT:
            if not can_run(language):
                continue
            with self.subTest(case=label):
                verdict, _ = self.checked(language, code, stdin, arguments)
                self.assertEqual(wanted, verdict["status"], verdict)


if __name__ == "__main__":
    unittest.main()
