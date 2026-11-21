import json
import sqlite3
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import devices  # noqa: E402
import phone  # noqa: E402
import store  # noqa: E402

OLD_ANDROID_TURN = {"model": "Arty", "messages": [{"role": "user", "content": "set an alarm for 7am"}], "client": "phone"}


def without_new_columns():
    with sqlite3.connect(store.DB_FILE) as connection:
        for table, column in (("schedules", "job"), ("approved_devices", "kind")):
            present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column in present:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


class TheLiveDatabase(Server):
    def test_a_database_from_before_the_merge_gains_the_new_columns_and_keeps_its_devices(self):
        without_new_columns()
        key = devices.new_device_key()
        with sqlite3.connect(store.DB_FILE) as connection:
            connection.execute("INSERT INTO approved_devices (name, address, key_hash, created) VALUES (?, ?, ?, ?)",
                               ("Old phone", "192.0.2.5", devices.key_hash(key), 1.0))
            connection.execute("INSERT INTO schedules (title, instructions, cron, description, next_run, created) "
                               "VALUES ('Old reminder', 'say hi', NULL, 'once', 9e9, 1.0)")
        store.init()
        self.assertIn(devices.key_hash(key), store.active_device_hashes())
        old = next(device for device in store.list_devices() if device["name"] == "Old phone")
        self.assertEqual(old["kind"], "")
        listed = next(row for row in store.list_schedules() if row["title"] == "Old reminder")
        self.assertIsNone(listed["job"])
        status, body = self.request("GET", "/v1/schedules", headers={"Authorization": f"Bearer {key}"}, authorised=False)
        self.assertEqual(status, 200, body)


class AndroidOneFiveEight(Server):
    def start_turn(self, body, key=None):
        seen = {}

        def turn(messages, model, client_tools=None, skip_groups=(), on_text=None, client="", **kwargs):
            seen["client"] = client
            return "ok", [], {}, None

        headers = {"Authorization": f"Bearer {key}"} if key else None
        with unittest.mock.patch.object(brain, "chat_jobs", brain.ChatJobs()), \
                unittest.mock.patch.object(brain, "run_turn", side_effect=turn):
            status, answer = self.request("POST", "/v1/chat/jobs", body=body, headers=headers, authorised=key is None)
            job_id = json.loads(answer).get("id")
            for _ in range(100):
                if brain.chat_jobs.get(job_id)["status"] != "running":
                    break
                time.sleep(0.02)
            done = brain.chat_jobs.get(job_id)
        return status, done, seen

    def test_its_chat_job_body_with_its_device_key_still_starts_a_turn_as_the_old_phone_app(self):
        key = devices.new_device_key()
        store.add_device("Pixel", "192.0.2.6", devices.key_hash(key))
        status, done, seen = self.start_turn(OLD_ANDROID_TURN, key)
        self.assertEqual(status, 202)
        self.assertEqual(done["answer"], "ok")
        self.assertEqual(seen["client"], phone.PHONE_CLIENT)

    def test_the_new_android_app_is_told_apart_by_what_it_declares(self):
        status, _, seen = self.start_turn({**OLD_ANDROID_TURN, "features": ["device_actions", "camera"]})
        self.assertEqual(status, 202)
        self.assertEqual(seen["client"], phone.ANDROID_CLIENT)

    def test_its_alarm_request_gets_no_card_it_cannot_show(self):
        self.assertIsNone(brain.phone_action("set an alarm for 7am", phone.PHONE_CLIENT))
        reply = phone.reply(brain.phone_action("text Sam saying on my way", phone.PHONE_CLIENT), phone.PHONE_CLIENT)
        line = reply.split("<phone-action>")[1].split("</phone-action>")[0]
        self.assertEqual(set(json.loads(line)), {"kind", "who", "body"})


if __name__ == "__main__":
    unittest.main()
