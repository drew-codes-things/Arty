[Arty](../README.md) / [Docs](README.md)

# Tested On and Test Results

## Tested On

| Part | Ran and tested on |
|---|---|
| Brain | Proxmox VE 9.2.21 (kernel 7.0.14-25-pve), LXC container on Debian 13 (trixie), Python 3.13.5, ArtyEngine with `qwen3:14b` on an NVIDIA RTX 3060 (12 GB, CUDA), the tool hub on mcp 2.3.0 with tool servers on mcp 1.30.0, SearXNG |
| Everything else | See each part's own README |

## Test Results

### Speed on the RTX 3060 (12 GB)

| Model | Context | Writing speed | Placement |
|---|---|---|---|
| qwen2.5:3b | - | about 120 tok/s | 100% GPU |
| qwen2.5:7b | 16k | 65 tok/s | 100% GPU, with qwen2.5vl:7b loaded too |
| qwen3:14b | 8k | 33 tok/s | 100% GPU (10 GB) |
| qwen3:14b | 12k | 33 tok/s | 100% GPU (11 GB) |
| qwen3:14b | 16k | 22 tok/s | 9% spills onto the CPU (12 GB) |
| qwen3:14b, q8_0 context cache | 12k | 29.6 tok/s | 100% GPU (9.8 GB, cache 1,020 MB instead of 1,920 MB) |
| qwen3:14b, q8_0 context cache | 24k | 28.6 tok/s | 100% GPU (10.8 GB) |
| qwen3:14b, q8_0 context cache | 32k | - | 11% spills onto the CPU |
| mimo-v2.6:9b (the code and picture model until 26 Nov 2026) | - | 53.4 to 53.9 tok/s | 100% GPU (6.6 GB) |
| Qwen3.6 35B A3B, UD-IQ4_XS (the main model since 26 Nov 2026) | 24k | about 15 tok/s, prompt read at about 37 tok/s | - |

### qwen2.5:7b and qwen3:14b, same prompts

| Test | qwen2.5:7b | qwen3:14b |
|---|---|---|
| Apples puzzle | Muddled: said "10 apples", then worked out 7 | 7, clean working |
| Film end time (19:50 + 2h47) | Long, cut off before answering | 22:37, correct |
| "How's my server?" with a tool available | Said it would check, but never called the tool | Called the tool straight away |
| Code one-liner | Fine but wordy | Fine and tight |
| Thinking mode on | n/a | 7, in 11 s |

### qwen3:14b weak spots, and what fixed them

A full run of 17 test questions passed 16; everything left over passed when re-tested after these fixes.

| Weak spot | Before | After |
|---|---|---|
| Dates ("days until Christmas", "what day is 14 Feb 2027", Easter, "90 days from today") | 25-80 s, sometimes wrong | Under 2 s, exact: worked out in code first |
| Currency | Web search, no number | ECB exchange rate, about 4 s |
| Units (F to C, miles to km, pints) | Grabbed the time-zone tool | `convert_units` |
| Sums and percentages | Did them in its head | `calculate` |
| Clock maths (film end times) | OK | `clock_maths` for exactness |
| "Write me a command" | Ran it instead of writing it | Gives just the code; "...and run it" runs it |
| All 65 tools offered at once | About 10,500 tokens of tool descriptions in what was then a 12,288-token context (24,576 now) | Capped at 40 tools per message, about 5,000 tokens |

### The honesty check, judged by qwen3:14b

| Test | Result |
|---|---|
| "All sorted, the living room lights are on now" with no tool run | Corrected to say it didn't turn them on |
| Same, in Spanish ("Tu recordatorio está listo para las 18:00") | Corrected, in Spanish |
| "Kuma is back up and running" after the restart tool failed | Corrected, giving the permission error |
| A desktop description after `look_at_desktop` | Left alone |
| "I've opened the page" after the browser really opened it | Wrongly flagged, so the check now only runs when no action tool worked or one failed |
| A plain answer ("Paris is the capital of France") | Left alone, in 0.4 s |

### Running the tests

```bash
cd brain && python3 -m unittest discover -s tests
(cd hub && /opt/ai-hub/bin/python -m unittest discover -s tests)   # needs mcp 2
python3 -m pytest -q mcp voice/tests tests arty-pc chat-bots   # needs pytest, mcp, aiohttp, bcrypt, numpy, webrtcvad-wheels
(cd code-runner && RUNNER_URL=http://<runner address>:8790 RUNNER_TOKEN=<the token> python3 -m unittest discover -s tests)   # live tests skip without RUNNER_URL
ARTY_URL=http://<AI_ADDRESS>:8081/v1 ARTY_KEY=... python3 tests/e2e_battery.py   # scored eval against the live brain, see --help
cd apps/ios && node --test tests/
```

Each app's README has its own.
