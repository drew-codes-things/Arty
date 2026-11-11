<div align="center">

# Arty Code

**Arty in your terminal: it reads, edits and runs code in the folder you start it in, and asks before it changes anything.**

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-none-success?style=flat-square)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../../LICENSE)

</div>

> **Arty Code is being replaced by Arty CLI**, its own project at
> [github.com/drew-codes-things/ArtyCode](https://github.com/drew-codes-things/ArtyCode). Arty CLI logs in to Arty,
> Claude or Gemini, and adds `/model`, hooks, MCP servers, skills and installers for Debian and Windows. Use Arty CLI
> for anything new; this folder stays only until the move is finished.

---

## Overview

`arty-code` is a coding assistant for the terminal on Debian (and other Linux) and Windows. You ask in plain words
("why does the login test fail?", "add a --dry-run flag"), and Arty looks through the project, makes the change and
runs the tests. Your files and commands never leave your computer except as the text Arty reads: the file tools and
the shell run locally, while the thinking happens on your Arty brain, which also brings its own tools (web search,
your homelab and the rest).

| Tool | What it does | Asks first? |
|---|---|---|
| `read_file`, `list_files`, `search` | Read and find code in the project | Only outside the project folder |
| `edit_file`, `write_file` | Change or create files, keeping each file's line endings | Yes, showing the diff (`--auto-edit` skips this inside the project) |
| `run_command` | Tests, builds, git (bash on Linux, PowerShell on Windows) | Yes, except look-only commands such as `ls`, `cat`, `git status`, `git diff` that stay inside the project and use no option that writes or runs something (`rg --pre`, `find -fprint`, `git log --output`, `git branch -D`, `tree -H`, `file -C` all ask). It also asks when a wildcard or `~+`/`~-` path reaches outside the project, when an option follows links (`grep -R`, `rg -L`, `find -L`), when a wildcard matches a file named like an option, when a path is glued to a short option or written with backslashes or a drive (`C:`, `Env:`), and for git when the repository's own config can run programs (`diff.external`, `textconv`, `fsmonitor`, includes) |
| `command_output`, `stop_command` | Read or stop a command started in the background (a dev server, a long build); background commands end when arty-code does | No |
| `todo_write` | A checklist for jobs with several steps, shown as it goes (`/todos`) | No |
| `web_fetch` | Read a web page (documentation, an issue), marked to Arty as information rather than instructions; a redirect to another site is not followed | Yes, once per site with **a** |
| `explore` | A helper with its own fresh conversation that looks through the project and reports back | It can only look |

Answer **y** (yes), **n** (no) or **a** (always). "Always" lasts until you quit: for edits it covers every edit, for a
command only that exact command. When there's no terminal to ask in (piped input), anything that needs an OK is refused.

`--permission-mode` (or `/mode` in the chat) sets how much is asked: `default`; `auto-edit` (edits inside the project
without asking); `plan` (look and plan only, nothing changed or run; `/plan` switches it); `allow-all` (nothing asked,
only for a folder you can throw away).

The default model is `arty/qwen2.5-coder:14b`, a coding model on the brain. `/model arty/qwen3:14b` (or any model
`GET /v1/models` lists) switches for the session.

## Install

Python 3.11 or newer, no other packages. With [pipx](https://pipx.pypa.io):

```bash
pipx install ./apps/beta/code
arty-code setup
```

On Windows, the same in PowerShell (`py -m pip install --user pipx`, then `py -m pipx install .\apps\beta\code`).

`arty-code setup` asks for the same details as Arty Desktop: the brain's address on your network
(`http://<AI_ADDRESS>:8081/v1`), optionally the address from anywhere (`https://<your address>/v1`) with its Cloudflare
Access client id and secret, and the API key. They are saved to `~/.config/arty-code/config.json`
(`%APPDATA%\arty-code\config.json` on Windows), readable only by you. The home address is tried first, then the other.
Before the key is sent to a plain `http://` home address, the brain there has to prove it knows the key (it answers
a random challenge at `/v1/identity` without the key being sent), so on another network whatever happens to sit at that
address never sees it. The check assumes `/v1/identity` is only reachable on your home network or behind Cloudflare
Access: if it were open to the internet, a fake server could pass the challenge on to the real brain. The proof is asked
again after five minutes, and a redirect from the brain is refused so the key is never sent on to another address.
`ARTY_URL`, `ARTY_REMOTE_URL`, `ARTY_API_KEY`, `ARTY_ACCESS_CLIENT_ID`, `ARTY_ACCESS_CLIENT_SECRET` and
`ARTY_CODE_MODEL` override the file.

## Use

```bash
cd my-project
arty-code                               # chat
arty-code -p "what does main.py do?"    # ask once and exit
arty-code --continue                    # carry on with the last conversation here
arty-code --resume <id>                 # an earlier one (/resume lists them)
```

| In the chat | What it does |
|---|---|
| `@path` | Attach a file; pictures go to Arty's vision |
| `# note` | Add a line to this project's `ARTY.md` |
| `!command` | Run a command yourself; Arty sees the output |
| `line \` | Carry on typing on the next line |
| `/init` | Arty looks through the project and writes `ARTY.md` (notes it reads every session) |
| `/review` | Review the uncommitted changes |
| `/compact` | Swap the conversation for a summary (also done automatically when it gets long) |
| `/model`, `/mode`, `/plan`, `/status`, `/todos`, `/memory`, `/resume`, `/clear`, `/help`, `/exit` | The rest |

`ARTY.md` in the project and in the config folder (notes for every project) are added to what Arty is told. The
first time you start arty-code in a folder that has its own `ARTY.md` or `.arty/commands`, it asks whether you trust
that folder (a cloned repo could put instructions there); until you say yes, they are left out. Adding a `# note`
trusts the folder.
Your own prompts go in `.arty/commands/<name>.md` (or `commands/` in the config folder) and run as `/<name> words`,
with `$ARGUMENTS` replaced by the words. Conversations are saved per project in the config folder's `sessions/`.
Ctrl-C stops the current request (what it already changed or ran stays noted in the conversation), Ctrl-D leaves.

## Limits

- The brain runs one model on the GPU at a time, so the first coding request after chatting with Arty (and the
  first chat after coding) waits while the other model loads.
- Beta: tested on Debian; the Windows build has not been run on Windows yet.
- Not yet: hooks, MCP servers, notebook editing and editor integration.

## Tests

```bash
cd apps/beta/code && python -m pytest -q tests
```
