import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_room_review3 import RoomOnFakeEngine, task_said  # noqa: E402

import model_choice  # noqa: E402
import models  # noqa: E402
import room  # noqa: E402
import room_models  # noqa: E402

MAIN_MODEL = "qwen3:14b"
CODING_ANSWER = "[file path=a.js]x[/file]"


class WithoutRouter(RoomOnFakeEngine):
    def setUp(self):
        super().setUp()
        self.ran_on = []
        self.make_room = unittest.mock.Mock(return_value=[])
        self.installed = unittest.mock.Mock(side_effect=AssertionError("the engine's models were listed"))
        for patch in (unittest.mock.patch.object(room_models, "router", return_value=None),
                      unittest.mock.patch.object(models, "installed", self.installed),
                      unittest.mock.patch.object(models, "make_room", self.make_room),
                      unittest.mock.patch.object(model_choice, "reload_main_model"),
                      unittest.mock.patch.object(room.agent_loop, "run_agent", side_effect=self.fake_run)):
            patch.start()
            self.addCleanup(patch.stop)

    def fake_run(self, run_config, state, run):
        self.ran_on.append(run_config["model"])
        return {"status": "done", "answer": CODING_ANSWER, "tools_used": [], "raw": {}}

    def test_a_coding_task_runs_on_the_main_model(self):
        self.assertEqual(self.reply(task_said("a.js")), CODING_ANSWER)
        self.assertEqual(self.ran_on, [MAIN_MODEL])

    def test_no_other_model_is_loaded_or_looked_for(self):
        self.reply(task_said("a.js"))
        self.make_room.assert_not_called()
        self.installed.assert_not_called()

    def test_a_main_model_the_settings_would_exclude_is_still_used(self):
        self.config["model"] = "house-special:14b"
        self.reply(task_said("a.js"))
        self.assertEqual(self.ran_on, ["house-special:14b"])

    def test_the_pick_is_the_main_model_and_nothing_is_switched(self):
        config = {"model": MAIN_MODEL}
        pick = room_models.choose(config, "work", "coding", 1000, 400.0, 500)
        self.assertEqual((pick.model, pick.role), (MAIN_MODEL, room_models.MAIN))
        self.assertFalse(room_models.use(config, pick))
        self.assertFalse(room_models.is_on(config))
        self.assertEqual(room_models.safe_main(config), MAIN_MODEL)

    def test_the_config_is_never_changed(self):
        config = {"model": MAIN_MODEL, "room": {"seconds_per_hour": 900}}
        self.assertIs(room_models.applied(config), config)
        self.assertIs(room_models.with_saved_seconds(config), config)

    def test_the_hourly_allowance_comes_from_the_config(self):
        self.config["room"] = {"seconds_per_hour": 900}
        self.assertEqual(room.hourly_allowance(), 900.0)


class RouterLoading(unittest.TestCase):
    def setUp(self):
        room_models.router.cache_clear()
        self.addCleanup(room_models.router.cache_clear)

    def test_no_add_on_folder_means_no_router(self):
        missing = ModuleNotFoundError("No module named", name=room_models.ADDON)
        with unittest.mock.patch("importlib.import_module", side_effect=missing):
            self.assertIsNone(room_models.router())

    def test_a_missing_module_inside_the_add_on_is_an_error(self):
        broken = ModuleNotFoundError("No module named", name="something_else")
        with unittest.mock.patch("importlib.import_module", side_effect=broken), self.assertRaises(ModuleNotFoundError):
            room_models.router()

    def test_an_add_on_without_a_router_means_no_router(self):
        with unittest.mock.patch("importlib.import_module", return_value=object()):
            self.assertIsNone(room_models.router())

    def test_the_router_is_what_the_add_on_offers(self):
        offered = object()
        addon = unittest.mock.Mock(**{room_models.ROUTER_ATTRIBUTE: offered})
        with unittest.mock.patch("importlib.import_module", return_value=addon):
            self.assertIs(room_models.router(), offered)


if __name__ == "__main__":
    unittest.main()
