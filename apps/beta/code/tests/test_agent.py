import json

from arty_code import agent, permissions, tools


def call(name, call_id="c1", **arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class ScriptedBrain:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def reply(self, messages, tools, on_text):
        self.sent.append({"messages": [dict(m) for m in messages], "tools": tools})
        content, calls = self.replies.pop(0)
        if content:
            on_text(content)
        return {"role": "assistant", "content": content, "tool_calls": calls}


class Screen:
    def __init__(self):
        self.text, self.activity = [], []

    def show_text(self, text):
        self.text.append(text)

    def show_activity(self, line):
        self.activity.append(line)


def test_a_fix_is_read_made_and_reported(tmp_path):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(
        ("", [call("read_file", path="app.py")]),
        ("", [call("edit_file", "c2", path="app.py", old_text="a - b", new_text="a + b")]),
        ("Fixed: add now adds.", []),
    )
    screen = Screen()
    messages = [{"role": "user", "content": "fix add"}]
    answer = agent.run_turn(messages, scripted, workspace, permissions.Gate(workspace, lambda q, p="": "y"), screen)
    assert answer == "Fixed: add now adds."
    assert (tmp_path / "app.py").read_text().endswith("a + b\n")
    assert messages[-1] == {"role": "assistant", "content": "Fixed: add now adds."}
    tool_results = [m for m in scripted.sent[2]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_results] == ["c1", "c2"]
    assert "1\tdef add" in tool_results[0]["content"]
    assert any("app.py" in line for line in screen.activity)


def test_a_refused_step_tells_the_model_not_to_work_round_it(tmp_path):
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(("", [call("run_command", command="rm -rf build")]), ("OK, I left it.", []))
    agent.run_turn([{"role": "user", "content": "clean"}], scripted, workspace,
                   permissions.Gate(workspace, permissions.refuse), Screen())
    result = [m for m in scripted.sent[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert result == agent.REFUSED


def test_the_same_call_over_and_over_stops_offering_tools(tmp_path):
    (tmp_path / "a.txt").write_text("a\n")
    workspace = tools.Workspace(tmp_path)
    same = ("", [call("read_file", path="a.txt")])
    scripted = ScriptedBrain(*([same] * agent.STUCK_REPEATS), ("Here's what I found.", []))
    answer = agent.run_turn([{"role": "user", "content": "look"}], scripted, workspace,
                            permissions.Gate(workspace, permissions.refuse), Screen())
    assert answer == "Here's what I found."
    assert scripted.sent[-1]["tools"] == []


def test_bad_arguments_from_the_model_are_reported_back(tmp_path):
    workspace = tools.Workspace(tmp_path)
    broken = {"id": "x", "type": "function", "function": {"name": "read_file", "arguments": "{oops"}}
    scripted = ScriptedBrain(("", [broken]), ("Sorry.", []))
    agent.run_turn([{"role": "user", "content": "read"}], scripted, workspace,
                   permissions.Gate(workspace, permissions.refuse), Screen())
    result = [m for m in scripted.sent[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert result.startswith("Error") and "JSON" in result


def test_a_long_job_stops_after_the_step_limit(tmp_path):
    (tmp_path / "a.txt").write_text("a\n")
    workspace = tools.Workspace(tmp_path)
    replies = [("", [call("read_file", f"c{i}", path="a.txt", offset=i + 1)]) for i in range(agent.MAX_ROUNDS + 1)]
    answer = agent.run_turn([{"role": "user", "content": "go"}], ScriptedBrain(*replies), workspace,
                            permissions.Gate(workspace, permissions.refuse), Screen())
    assert answer == agent.STEP_LIMIT_REACHED


def test_an_explore_helper_looks_around_on_its_own_and_reports_back(tmp_path):
    (tmp_path / "a.py").write_text("SECRET = 1\n")
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(
        ("", [call("explore", task="where is SECRET defined?")]),
        ("", [call("search", "s1", pattern="SECRET")]),
        ("It's in a.py line 1.", []),
        ("SECRET is defined in a.py.", []),
    )
    answer = agent.run_turn([{"role": "user", "content": "find SECRET"}], scripted, workspace,
                            permissions.Gate(workspace, permissions.refuse), Screen())
    assert answer == "SECRET is defined in a.py."
    helper_request = scripted.sent[1]
    assert helper_request["messages"][-1]["content"] == "where is SECRET defined?"
    assert {d["function"]["name"] for d in helper_request["tools"]} <= set(agent.EXPLORE_TOOLS)
    result = [m for m in scripted.sent[3]["messages"] if m["role"] == "tool"][0]["content"]
    assert result == "It's in a.py line 1."


def test_the_explore_helper_cannot_change_anything(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(
        ("", [call("explore", task="tidy a.py")]),
        ("", [call("write_file", "w1", path="a.py", content="gone")]),
        ("I couldn't change it.", []),
        ("Done looking.", []),
    )
    agent.run_turn([{"role": "user", "content": "go"}], scripted, workspace,
                   permissions.Gate(workspace, lambda q, p="": "y"), Screen())
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_changes_and_commands_that_ran_are_recorded(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(
        ("", [call("edit_file", "e1", path="a.py", old_text="x = 1", new_text="x = 2"),
              call("run_command", "r1", command="rm -rf build")]),
        ("", [call("read_file", "r2", path="a.py")]),
        ("Done.", []),
    )
    asked = iter(["y", "n"])
    done = []
    agent.run_turn([{"role": "user", "content": "go"}], scripted, workspace,
                   permissions.Gate(workspace, lambda q, p="": next(asked)), Screen(), done=done)
    assert done == ["edit_file a.py"]


def test_the_conversation_can_be_squeezed_between_rounds(tmp_path):
    (tmp_path / "a.txt").write_text("a\n")
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(("", [call("read_file", path="a.txt")]), ("Read it.", []))
    seen = []
    agent.run_turn([{"role": "user", "content": "look"}], scripted, workspace,
                   permissions.Gate(workspace, permissions.refuse), Screen(), squeeze=lambda m: seen.append(len(m)))
    assert seen == [3]


def test_a_command_cut_short_by_ctrl_c_is_still_recorded(tmp_path, monkeypatch):
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(("", [call("run_command", command="make deploy")]))

    def interrupted(name, arguments, workspace):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent.tools, "run", interrupted)
    done = []
    try:
        agent.run_turn([{"role": "user", "content": "go"}], scripted, workspace,
                       permissions.Gate(workspace, lambda q, p="": "y"), Screen(), done=done)
    except KeyboardInterrupt:
        pass
    assert done == ["run_command make deploy (stopped part way)"]


def test_ctrl_c_while_being_asked_records_nothing(tmp_path):
    workspace = tools.Workspace(tmp_path)
    scripted = ScriptedBrain(("", [call("run_command", command="make deploy")]))

    def ctrl_c(question, preview=""):
        raise KeyboardInterrupt

    done = []
    try:
        agent.run_turn([{"role": "user", "content": "go"}], scripted, workspace, permissions.Gate(workspace, ctrl_c),
                       Screen(), done=done)
    except KeyboardInterrupt:
        pass
    assert done == []
