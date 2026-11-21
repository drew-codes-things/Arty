import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HUB_FOLDER = Path(__file__).resolve().parents[1]
REPO = HUB_FOLDER.parent
FAKE_TOOLS = Path(__file__).resolve().parent / "fake_tools.py"
KEY = "hub-test-key-0123456789"
START_SECONDS = 60
RESTART_SECONDS = 30
OWN_APPS = {"drive": "https://drive.example.org", "calendar": "https://calendar.example.org",
            "starfire": "https://starfire.example.org"}

sys.path.insert(0, str(HUB_FOLDER))

import tool_hub  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def request(port, method, path, body=None, key=KEY, raw=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        text = response.read().decode()
    finally:
        connection.close()
    return response.status, (json.loads(text) if text else None)


def resolved(node, schemas, depth=0):
    if depth > 8 or not isinstance(node, (dict, list)):
        return node
    if isinstance(node, list):
        return [resolved(item, schemas, depth + 1) for item in node]
    if "$ref" in node:
        return resolved(schemas[node["$ref"].split("/")[-1]], schemas, depth + 1)
    return {key: resolved(value, schemas, depth + 1) for key, value in node.items()}


class RunningHub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        config = Path(cls.folder.name) / "servers.json"
        config.write_text(json.dumps({"mcpServers": {
            "fake": {"command": sys.executable, "args": [str(FAKE_TOOLS)], "env": {"FAKE_SETTING": "on"}},
            "fetch": {"command": sys.executable, "args": [str(FAKE_TOOLS)]},
            "research": {"command": sys.executable, "args": [str(FAKE_TOOLS)]},
            "missing": {"command": "/nonexistent/tool-server"},
        }}))
        cls.own_apps = Path(cls.folder.name) / "own-apps.json"
        cls.own_apps.write_text(json.dumps(OWN_APPS))
        cls.port = free_port()
        cls.process = subprocess.Popen(
            [sys.executable, str(HUB_FOLDER / "tool_hub.py"), "--host", "127.0.0.1", "--port", str(cls.port), "--config", str(config),
             "--own-apps", str(cls.own_apps)],
            env={**os.environ, "MCPO_API_KEY": KEY}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + START_SECONDS
        while time.time() < deadline:
            try:
                status, _ = request(cls.port, "GET", "/fake/openapi.json")
                if status == 200:
                    return
            except OSError:
                pass
            if cls.process.poll() is not None:
                break
            time.sleep(0.3)
        cls.process.kill()
        raise RuntimeError("the hub did not start: " + (cls.process.stdout.read() if cls.process.stdout else ""))

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
        cls.process.stdout.close()
        cls.folder.cleanup()

    def spec(self):
        status, spec = request(self.port, "GET", "/fake/openapi.json")
        self.assertEqual(status, 200)
        return spec

    def test_every_tool_is_a_post_path_with_its_description_and_body_schema(self):
        spec = self.spec()
        self.assertEqual(set(spec["paths"]), {"/echo", "/add", "/stock", "/fetch", "/read_document", "/fail", "/crash",
                                              "/environment"})
        operation = spec["paths"]["/add"]["post"]
        self.assertEqual(operation["description"], "Adds two numbers.")
        reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema = spec["components"]["schemas"][reference.split("/")[-1]]
        self.assertEqual(set(schema["properties"]), {"first", "second"})
        self.assertEqual(set(schema["required"]), {"first", "second"})

    def test_nested_shapes_resolve_from_the_components(self):
        spec = self.spec()
        schemas = spec["components"]["schemas"]
        reference = spec["paths"]["/stock"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        item = resolved(schemas[reference.split("/")[-1]], schemas)["properties"]["item"]
        self.assertEqual(set(item["properties"]), {"name", "count"})
        self.assertNotIn("$defs", json.dumps(spec))

    def test_a_call_returns_text_as_text_and_json_as_json(self):
        self.assertEqual(request(self.port, "POST", "/fake/echo", {"text": "hello there"}), (200, "hello there"))
        self.assertEqual(request(self.port, "POST", "/fake/add", {"first": 2, "second": 3}), (200, 5))
        self.assertEqual(request(self.port, "POST", "/fake/stock", {"item": {"name": "tea"}}), (200, "1 x tea"))

    def test_a_tool_error_is_a_500_with_its_message(self):
        status, body = request(self.port, "POST", "/fake/fail", {})
        self.assertEqual(status, 500)
        self.assertIn("the fake tool broke", body["detail"]["message"])

    def test_unknown_tools_and_servers_are_404(self):
        self.assertEqual(request(self.port, "POST", "/fake/nope", {})[0], 404)
        self.assertEqual(request(self.port, "POST", "/nobody/echo", {})[0], 404)
        self.assertEqual(request(self.port, "GET", "/nobody/openapi.json")[0], 404)

    def test_everything_needs_the_key(self):
        for key in ("", "wrong-key"):
            self.assertIn(request(self.port, "GET", "/fake/openapi.json", key=key)[0], (401, 403))
            self.assertIn(request(self.port, "POST", "/fake/echo", {"text": "x"}, key=key)[0], (401, 403))

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        self.assertEqual(request(self.port, "POST", "/fake/echo", raw=b"{not json")[0], 400)
        self.assertEqual(request(self.port, "POST", "/fake/echo", raw=b"[1, 2]")[0], 400)

    def test_an_empty_body_calls_with_no_arguments(self):
        status, body = request(self.port, "POST", "/fake/environment", raw=b"")
        self.assertEqual(status, 200)
        self.assertEqual(body["setting"], "on")

    def test_tool_servers_get_their_settings_but_not_the_hub_key(self):
        status, body = request(self.port, "POST", "/fake/environment", {})
        self.assertEqual((status, body), (200, {"has_hub_key": False, "setting": "on"}))

    def test_a_server_that_cannot_start_does_not_stop_the_others(self):
        self.assertEqual(request(self.port, "GET", "/missing/openapi.json")[0], 503)
        self.assertEqual(request(self.port, "POST", "/missing/anything", {})[0], 503)
        self.assertEqual(request(self.port, "POST", "/fake/echo", {"text": "still here"}), (200, "still here"))

    def test_the_fetch_server_never_reads_an_own_app_or_the_starfire_host(self):
        for url in ("https://drive.example.org/files/0", "https://STARFIRE.example.org./c/1", "https://api.calendar.example.org/x",
                    "http://drive.example.org:8443/"):
            with self.subTest(url=url):
                status, body = request(self.port, "POST", "/fetch/fetch", {"url": url})
                self.assertEqual(status, 403)
                self.assertIn("one of the user's own apps", body["detail"])

    def test_an_own_app_written_another_way_is_not_fetched(self):
        for url in ("https://\uff44rive.example.org/api/session", "https://drive\u3002example\u3002org/api/session",
                    "https://drive%2Eexample.org/api/session"):
            with self.subTest(url=url):
                status, body = request(self.port, "POST", "/fetch/fetch", {"url": url})
                self.assertEqual(status, 403)
                self.assertIn("one of the user's own apps", body["detail"])

    def test_an_address_with_a_backslash_or_control_character_is_not_fetched(self):
        for url in ("https://drive.example.org\\api/session", "https:\\\\drive.example.org/", "https://dri\x01ve.example.org/"):
            with self.subTest(url=url):
                status, body = request(self.port, "POST", "/fetch/fetch", {"url": url})
                self.assertEqual(status, 403)

    def test_a_document_is_never_read_from_an_own_app(self):
        for url in ("https://drive.example.org/api/files/2/content", "https://\uff44rive.example.org/x.pdf",
                    "https://drive.example.org\\api/x.pdf"):
            with self.subTest(url=url):
                status, body = request(self.port, "POST", "/research/read_document", {"path_or_url": url})
                self.assertEqual(status, 403)

    def test_other_documents_are_still_read(self):
        for target in ("https://news.example.com/report.pdf", "notes/plan.docx"):
            with self.subTest(target=target):
                self.assertEqual(request(self.port, "POST", "/research/read_document", {"path_or_url": target}),
                                 (200, f"read {target}"))

    def test_the_fetch_server_still_reads_other_pages(self):
        for url in ("https://news.example.com/story", "https://drive.example.org.evil.example/"):
            with self.subTest(url=url):
                self.assertEqual(request(self.port, "POST", "/fetch/fetch", {"url": url}), (200, f"read {url}"))

    def test_other_servers_are_not_checked(self):
        self.assertEqual(request(self.port, "POST", "/fake/fetch", {"url": "https://drive.example.org/"}),
                         (200, "read https://drive.example.org/"))

    def test_an_unreadable_list_stops_the_fetch_server(self):
        self.own_apps.write_text("{broken")
        try:
            status, body = request(self.port, "POST", "/fetch/fetch", {"url": "https://news.example.com/story"})
        finally:
            self.own_apps.write_text(json.dumps(OWN_APPS))
        self.assertEqual(status, 403)
        self.assertIn("can't be read", body["detail"])

    def test_zz_a_server_that_dies_is_started_again(self):
        status, _ = request(self.port, "POST", "/fake/crash", {})
        self.assertGreaterEqual(status, 500)
        deadline = time.time() + RESTART_SECONDS
        while time.time() < deadline:
            if request(self.port, "POST", "/fake/echo", {"text": "back"}) == (200, "back"):
                return
            time.sleep(0.5)
        self.fail("the fake server was not started again")

    def test_the_brain_reads_the_tools_and_calls_them(self):
        os.environ.setdefault("BRAIN_API_KEY", "test")
        os.environ["MCPO_API_KEY"] = KEY
        os.environ["HUB_URL"] = f"http://127.0.0.1:{self.port}"
        sys.path.insert(0, str(REPO / "brain"))
        import connections
        connections.HUB_URL = os.environ["HUB_URL"]
        connections.HUB_KEY = KEY
        connections.SERVERS = ["fake"]
        hub = connections.ToolHub()
        hub.refresh()
        names = {tool["function"]["name"] for tool in hub.tools}
        self.assertIn("fake__stock", names)
        stock = next(tool for tool in hub.tools if tool["function"]["name"] == "fake__stock")["function"]
        self.assertEqual(set(stock["parameters"]["properties"]["item"]["properties"]), {"name", "count"})
        self.assertEqual(hub.call("fake__add", {"first": 1, "second": 1}), "2")
        self.assertIn("the fake tool broke", hub.call("fake__fail", {}))


class Settings(unittest.TestCase):
    def test_the_hub_will_not_start_without_a_key(self):
        environment = {key: value for key, value in os.environ.items() if key != "MCPO_API_KEY"}
        done = subprocess.run([sys.executable, str(HUB_FOLDER / "tool_hub.py"), "--config", "/nonexistent.json"],
                              env=environment, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("MCPO_API_KEY", done.stderr)

    def test_the_config_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "servers.json"
            for bad in ({}, {"mcpServers": []}, {"mcpServers": {"a b": {"command": "x"}}}, {"mcpServers": {"a": {}}},
                        {"mcpServers": {"a": {"command": "x", "args": "not a list"}}},
                        {"mcpServers": {"a": {"command": "x", "env": {"K": 1}}}},
                        {"mcpServers": {"a": {"url": "https://example.org/mcp"}}}):
                config.write_text(json.dumps(bad))
                with self.assertRaises(ValueError, msg=json.dumps(bad)):
                    tool_hub.load_servers(config)

    def test_the_shipped_config_loads(self):
        servers = tool_hub.load_servers(REPO / "mcp" / "tool-hub.json")
        self.assertEqual(list(servers), ["memory", "time", "fetch", "sequential-thinking", "workspace", "research", "homelab"])


if __name__ == "__main__":
    unittest.main()
