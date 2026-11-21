import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import devices  # noqa: E402
import phone_link  # noqa: E402
import spake2  # noqa: E402
import store  # noqa: E402

VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "phone_link_vectors.json").read_text())
ACCESS = {"id": "client-id.access", "secret": "client-secret-for-test"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Phone:
    def __init__(self, code, secret=None):
        self.code = code
        self.secret = secret or spake2.new_secret()
        self.share = spake2.share_hex(self.secret, code, spake2.APP)

    def key(self, arty_share):
        return spake2.shared_key(self.secret, self.code, spake2.APP, self.share, arty_share)


def issued_into(saved):
    def issue(name, key):
        saved.append((name, key))
        return len(saved)
    return issue


class SharedVectors(unittest.TestCase):
    def test_the_vectors_the_phone_apps_test_against_match_this_brain(self):
        code = VECTORS["code"]
        phone_secret, arty_secret = int(VECTORS["phone_secret_hex"], 16), int(VECTORS["arty_secret_hex"], 16)
        self.assertEqual(spake2.CONTEXT.decode(), VECTORS["context"])
        self.assertEqual(format(spake2.BLINDS[spake2.APP], "x"), VECTORS["blind_phone_hex"])
        self.assertEqual(format(spake2.BLINDS[spake2.ARTY], "x"), VECTORS["blind_arty_hex"])
        self.assertEqual(spake2.share_hex(phone_secret, code, spake2.APP), VECTORS["phone_share"])
        self.assertEqual(spake2.share_hex(arty_secret, code, spake2.ARTY), VECTORS["arty_share"])
        key = spake2.shared_key(arty_secret, code, spake2.ARTY, VECTORS["phone_share"], VECTORS["arty_share"])
        self.assertEqual(key.hex(), VECTORS["key_hex"])
        self.assertEqual(spake2.proof(key, spake2.APP), VECTORS["phone_proof"])
        self.assertEqual(spake2.proof(key, spake2.SEALING_APP), VECTORS["phone_sealing_proof"])
        self.assertEqual(spake2.proof(key, spake2.ARTY), VECTORS["arty_proof"])
        self.assertEqual(phone_link.phone_key(key), VECTORS["device_key"])

    def test_the_sealed_access_vector_the_phone_apps_open_matches_this_brain(self):
        key = bytes.fromhex(VECTORS["key_hex"])
        sealed = spake2.seal(key, VECTORS["sealed_plain"].encode(), bytes.fromhex(VECTORS["seal_nonce_hex"]))
        self.assertEqual(sealed, VECTORS["sealed_access"])
        self.assertEqual(spake2.open_sealed(key, sealed).decode(), VECTORS["sealed_plain"])

    def test_a_share_must_lie_in_the_prime_order_subgroup(self):
        for share in VECTORS["shares_in_group"]:
            self.assertEqual(spake2.element_from_hex(share), int(share, 16))
        for share in VECTORS["shares_refused"]:
            with self.assertRaises(spake2.BadShare, msg=share[-8:]):
                spake2.element_from_hex(share)

    def test_a_phone_share_outside_the_subgroup_never_starts_a_link(self):
        offers = phone_link.Offers()
        made = offers.create()
        for share in VECTORS["shares_refused"]:
            with self.assertRaises(phone_link.LinkRefused, msg=share[-8:]) as refused:
                offers.begin(made["offer"], "phone", share)
            self.assertEqual(refused.exception.status, 400)

    def test_phone_linking_cannot_be_mixed_up_with_the_desktop_link(self):
        self.assertEqual(spake2.CONTEXT, b"arty-phone-link-v1")


class Offers(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.offers = phone_link.Offers(self.clock)

    def linked(self, made, name="Pixel 8", phone=None):
        phone = phone or Phone(made["code"])
        begun = self.offers.begin(made["offer"], name, phone.share)
        key = phone.key(begun["share"])
        self.assertEqual(begun["proof"], spake2.proof(key, spake2.ARTY))
        saved = []
        finished = self.offers.finish(made["offer"], begun["session"], spake2.proof(key, spake2.APP), issued_into(saved))
        return finished, saved, key

    def test_a_scanned_code_links_once_and_both_sides_hold_the_same_key_without_sending_it(self):
        made = self.offers.create(ACCESS)
        finished, saved, key = self.linked(made)
        self.assertEqual(finished, {"linked": True, "name": "Pixel 8", "access": ACCESS})
        self.assertEqual(saved, [("Pixel 8", phone_link.phone_key(key))])
        self.assertTrue(saved[0][1].startswith(devices.DEVICE_KEY_PREFIX))
        self.assertNotIn(saved[0][1], json.dumps(finished))
        self.assertEqual(self.offers.status(made["offer"]), {"state": "linked", "name": "Pixel 8", "device_id": 1})

    def test_a_phone_that_can_open_sealed_access_never_gets_it_in_the_clear(self):
        made = self.offers.create(ACCESS)
        phone = Phone(made["code"])
        begun = self.offers.begin(made["offer"], "Pixel 8", phone.share)
        key = phone.key(begun["share"])
        finished = self.offers.finish(made["offer"], begun["session"], spake2.proof(key, spake2.APP), issued_into([]),
                                      seal=phone_link.SEALED_ACCESS_VERSION)
        self.assertNotIn("access", finished)
        self.assertNotIn(ACCESS["secret"], json.dumps(finished))
        self.assertEqual(json.loads(spake2.open_sealed(key, finished["sealed_access"])), ACCESS)

    def test_a_phone_proving_it_seals_gets_sealed_access_even_if_the_flag_was_stripped_on_the_way(self):
        made = self.offers.create(ACCESS)
        phone = Phone(made["code"])
        begun = self.offers.begin(made["offer"], "Pixel 8", phone.share)
        key = phone.key(begun["share"])
        finished = self.offers.finish(made["offer"], begun["session"], spake2.proof(key, spake2.SEALING_APP), issued_into([]))
        self.assertNotIn("access", finished)
        self.assertNotIn(ACCESS["secret"], json.dumps(finished))
        self.assertEqual(json.loads(spake2.open_sealed(key, finished["sealed_access"])), ACCESS)

    def test_the_sealing_proof_cannot_be_made_without_the_link_key(self):
        made = self.offers.create(ACCESS)
        phone = Phone(made["code"])
        begun = self.offers.begin(made["offer"], "Pixel 8", phone.share)
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.finish(made["offer"], begun["session"], spake2.proof(bytes(32), spake2.SEALING_APP), issued_into([]))
        self.assertEqual(refused.exception.status, 403)

    def test_sealed_access_that_was_changed_on_the_way_does_not_open(self):
        key = bytes(range(32))
        sealed = spake2.seal(key, b'{"id":"a","secret":"b"}')
        for broken in (sealed[:-2] + ("00" if sealed[-2:] != "00" else "11"), "00" + sealed[2:], sealed[:40], "zz", ""):
            with self.assertRaises(spake2.BadSeal):
                spake2.open_sealed(key, broken)
        with self.assertRaises(spake2.BadSeal):
            spake2.open_sealed(bytes(32), sealed)

    def test_an_older_phone_that_does_not_ask_for_sealing_still_gets_the_pair(self):
        finished, _, _ = self.linked(self.offers.create(ACCESS))
        self.assertEqual(finished["access"], ACCESS)
        self.assertNotIn("sealed_access", finished)

    def test_the_code_only_holds_a_short_lived_offer(self):
        made = self.offers.create(ACCESS)
        self.assertEqual(set(made), {"offer", "code", "expires_in"})
        self.assertEqual(made["expires_in"], phone_link.OFFER_SECONDS)
        self.assertNotIn(ACCESS["secret"], json.dumps(made))

    def test_a_used_code_is_useless_afterwards(self):
        made = self.offers.create()
        self.linked(made)
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.begin(made["offer"], "photo of the screen", Phone(made["code"]).share)
        self.assertEqual(refused.exception.status, 410)

    def test_a_code_photographed_and_scanned_after_two_minutes_is_refused(self):
        made = self.offers.create()
        self.clock.now += phone_link.OFFER_SECONDS + 1
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.begin(made["offer"], "late", Phone(made["code"]).share)
        self.assertEqual(refused.exception.status, 410)
        self.assertEqual(self.offers.status(made["offer"]), {"state": "gone"})

    def test_a_phone_that_does_not_know_the_code_cannot_finish_and_uses_the_code_up(self):
        made = self.offers.create(ACCESS)
        guesser = Phone("ff" * 16)
        begun = self.offers.begin(made["offer"], "guesser", guesser.share)
        wrong_key = guesser.key(begun["share"])
        saved = []
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.finish(made["offer"], begun["session"], spake2.proof(wrong_key, spake2.APP), issued_into(saved))
        self.assertEqual(refused.exception.status, 403)
        self.assertEqual(saved, [])
        with self.assertRaises(phone_link.LinkRefused):
            self.linked(made)

    def test_a_finish_from_another_session_is_refused(self):
        made = self.offers.create()
        phone = Phone(made["code"])
        begun = self.offers.begin(made["offer"], "phone", phone.share)
        key = phone.key(begun["share"])
        with self.assertRaises(phone_link.LinkRefused):
            self.offers.finish(made["offer"], "0" * 32, spake2.proof(key, spake2.APP), issued_into([]))

    def test_too_many_starts_on_one_code_use_it_up(self):
        made = self.offers.create()
        for _ in range(phone_link.MAX_BEGINS_PER_OFFER):
            self.offers.begin(made["offer"], "phone", Phone(made["code"]).share)
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.begin(made["offer"], "phone", Phone(made["code"]).share)
        self.assertEqual(refused.exception.status, 410)

    def test_a_bad_share_is_refused_before_anything_is_counted(self):
        made = self.offers.create()
        for share in ("", "00" * 256, "zz", None, 7):
            with self.assertRaises(phone_link.LinkRefused) as refused:
                self.offers.begin(made["offer"], "phone", share)
            self.assertEqual(refused.exception.status, 400)
        self.linked(made)

    def test_junk_offers_get_a_cheap_answer_and_never_lock_out_a_real_phone(self):
        made = self.offers.create()
        share = Phone(made["code"]).share
        for caller in range(phone_link.MAX_BEGINS_PER_MINUTE + 5):
            with self.assertRaises(phone_link.LinkRefused) as refused:
                self.offers.begin(f"not-a-real-offer-{caller}", "attacker", share, f"203.0.113.{caller}")
            self.assertEqual(refused.exception.status, 410)
        self.linked(made)

    def test_one_address_that_keeps_guessing_offers_is_turned_away_for_a_minute(self):
        made = self.offers.create()
        share = Phone(made["code"]).share
        for attempt in range(phone_link.MAX_UNKNOWN_BEGINS_PER_MINUTE):
            with self.assertRaises(phone_link.LinkRefused) as refused:
                self.offers.begin(f"guess-{attempt}", "x", share, "203.0.113.9")
            self.assertEqual(refused.exception.status, 410)
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.begin(made["offer"], "x", share, "203.0.113.9")
        self.assertEqual(refused.exception.status, 429)
        begun = self.offers.begin(made["offer"], "phone", share, "172.31.255.9")
        self.assertIn("session", begun)
        self.clock.now += phone_link.BEGIN_WINDOW_SECONDS + 1
        with self.assertRaises(phone_link.LinkRefused) as refused:
            self.offers.begin("guess-again", "x", share, "203.0.113.9")
        self.assertEqual(refused.exception.status, 410)

    def test_an_unknown_offer_is_answered_without_any_big_number_sums(self):
        share = Phone("ab" * 16).share
        with unittest.mock.patch.object(spake2, "element_from_hex", side_effect=AssertionError("modexp ran")), \
                unittest.mock.patch.object(spake2, "share_hex", side_effect=AssertionError("modexp ran")):
            with self.assertRaises(phone_link.LinkRefused) as refused:
                self.offers.begin("no-such-offer", "x", share, "203.0.113.5")
        self.assertEqual(refused.exception.status, 410)

    def test_the_addresses_remembered_for_guessing_stay_few(self):
        share = Phone("ab" * 16).share
        for caller in range(phone_link.MAX_TRACKED_CALLERS + 50):
            with self.assertRaises(phone_link.LinkRefused):
                self.offers.begin("nope", "x", share, f"caller-{caller}")
        self.assertLessEqual(len(self.offers.unknown_begins), phone_link.MAX_TRACKED_CALLERS)

    def test_only_a_few_codes_wait_at_once(self):
        made = [self.offers.create() for _ in range(phone_link.MAX_OFFERS + 2)]
        self.assertEqual(self.offers.status(made[0]["offer"]), {"state": "gone"})
        self.assertEqual(self.offers.status(made[-1]["offer"])["state"], "waiting")

    def test_the_access_pair_is_checked(self):
        for bad in ({"id": "a"}, {"id": "a", "secret": "b c"}, {"id": "a", "secret": 5}, "text",
                    {"id": "a", "secret": "x" * (phone_link.MAX_ACCESS_CHARS + 1)}, {"id": "a\n", "secret": "b"}):
            with self.assertRaises(phone_link.LinkRefused):
                self.offers.create(bad)

    def test_an_unknown_offer_is_gone(self):
        self.assertEqual(self.offers.status("nope"), {"state": "gone"})
        self.assertEqual(self.offers.status(None), {"state": "gone"})


class OverHttp(Server):
    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(brain, "phone_offers", phone_link.Offers())
        patcher.start()
        self.addCleanup(patcher.stop)
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM approved_devices")

    def post(self, path, body, headers=None, authorised=False):
        status, answer = self.request("POST", path, body=body, headers=headers, authorised=authorised)
        return status, json.loads(answer)

    def offer(self, access=None):
        status, made = self.post("/v1/phone-link/offers", {"access": access} if access else {}, authorised=True)
        self.assertEqual(status, 201)
        return made

    def link(self, made, name="Sam's Pixel"):
        phone = Phone(made["code"])
        status, begun = self.post("/link/phone/begin", {"offer": made["offer"], "name": name, "share": phone.share})
        self.assertEqual(status, 200, begun)
        key = phone.key(begun["share"])
        status, finished = self.post("/link/phone/finish", {"offer": made["offer"], "session": begun["session"],
                                                            "proof": spake2.proof(key, spake2.APP)})
        return status, finished, phone_link.phone_key(key)

    def test_a_linked_phone_uses_arty_with_its_own_key_and_gets_the_access_pair(self):
        status, finished, key = self.link(self.offer(ACCESS))
        self.assertEqual((status, finished["access"]), (200, ACCESS))
        self.assertEqual(self.request("GET", "/v1/tasks", headers={"Authorization": f"Bearer {key}"}, authorised=False)[0], 200)

    def test_over_http_a_new_phone_gets_the_pair_only_sealed_under_its_link_key(self):
        made = self.offer(ACCESS)
        phone = Phone(made["code"])
        _, begun = self.post("/link/phone/begin", {"offer": made["offer"], "name": "Pixel", "share": phone.share})
        key = phone.key(begun["share"])
        status, raw = self.request("POST", "/link/phone/finish", body={
            "offer": made["offer"], "session": begun["session"], "proof": spake2.proof(key, spake2.APP), "seal": 1},
            authorised=False)
        self.assertEqual(status, 200)
        self.assertNotIn(ACCESS["secret"], raw if isinstance(raw, str) else raw.decode())
        self.assertNotIn(ACCESS["id"], raw if isinstance(raw, str) else raw.decode())
        self.assertEqual(json.loads(spake2.open_sealed(key, json.loads(raw)["sealed_access"])), ACCESS)

    def test_over_http_a_sealing_phone_gets_the_pair_sealed_without_any_flag(self):
        made = self.offer(ACCESS)
        phone = Phone(made["code"])
        _, begun = self.post("/link/phone/begin", {"offer": made["offer"], "name": "Pixel", "share": phone.share})
        key = phone.key(begun["share"])
        status, raw = self.request("POST", "/link/phone/finish", body={
            "offer": made["offer"], "session": begun["session"], "proof": spake2.proof(key, spake2.SEALING_APP)},
            authorised=False)
        self.assertEqual(status, 200)
        self.assertNotIn(ACCESS["secret"], raw if isinstance(raw, str) else raw.decode())
        self.assertEqual(json.loads(spake2.open_sealed(key, json.loads(raw)["sealed_access"])), ACCESS)

    def test_drew_sees_the_phone_and_removing_it_cuts_it_off(self):
        made = self.offer()
        _, _, key = self.link(made)
        listed = json.loads(self.request("GET", "/v1/devices")[1])["devices"]
        self.assertEqual([(device["name"], device["kind"]) for device in listed], [("Sam's Pixel", "phone")])
        followed = json.loads(self.request("GET", f"/v1/phone-link/offers/{made['offer']}")[1])
        self.assertEqual(followed, {"state": "linked", "name": "Sam's Pixel", "device_id": listed[0]["id"]})
        self.assertEqual(self.request("DELETE", f"/v1/devices/{listed[0]['id']}")[0], 200)
        self.assertEqual(self.request("GET", "/v1/tasks", headers={"Authorization": f"Bearer {key}"}, authorised=False)[0], 401)

    def test_the_same_code_links_only_one_phone(self):
        made = self.offer()
        self.assertEqual(self.link(made)[0], 200)
        status, begun = self.post("/link/phone/begin", {"offer": made["offer"], "name": "second", "share": Phone(made["code"]).share})
        self.assertEqual(status, 410)
        self.assertIn("Show a new one", begun["error"]["message"])

    def test_only_arty_s_own_key_makes_and_follows_codes(self):
        _, _, phone_key = self.link(self.offer())
        as_phone = {"Authorization": f"Bearer {phone_key}"}
        self.assertEqual(self.post("/v1/phone-link/offers", {}, headers=as_phone)[0], 403)
        self.assertEqual(self.request("GET", "/v1/phone-link/offers/x", headers=as_phone, authorised=False)[0], 403)
        self.assertEqual(self.post("/v1/phone-link/offers", {})[0], 401)

    def test_the_phone_steps_need_no_key_but_a_wrong_proof_gets_nothing(self):
        made = self.offer(ACCESS)
        guesser = Phone("ab" * 16)
        _, begun = self.post("/link/phone/begin", {"offer": made["offer"], "name": "x", "share": guesser.share})
        status, answer = self.post("/link/phone/finish", {"offer": made["offer"], "session": begun["session"],
                                                          "proof": spake2.proof(guesser.key(begun["share"]), spake2.APP)})
        self.assertEqual(status, 403)
        self.assertNotIn(ACCESS["secret"], json.dumps(answer))
        self.assertEqual(json.loads(self.request("GET", "/v1/devices")[1])["devices"], [])

    def test_bad_bodies_are_plain_errors(self):
        self.assertEqual(self.post("/link/phone/begin", {"offer": "x", "name": 5, "share": "00"})[0], 400)
        self.assertEqual(self.post("/link/phone/finish", {"offer": "x"})[0], 410)
        status, _ = self.request("POST", "/link/phone/begin", body={"name": "x" * 5000}, authorised=False)
        self.assertEqual(status, 413)

    def test_junk_begins_over_http_do_not_stop_the_real_phone(self):
        made = self.offer()
        share = Phone("ab" * 16).share
        for attempt in range(phone_link.MAX_BEGINS_PER_MINUTE):
            status, _ = self.post("/link/phone/begin", {"offer": f"junk-{attempt}", "name": "x", "share": share})
            self.assertIn(status, (410, 429))
        status, _ = self.post("/link/phone/begin", {"offer": made["offer"], "name": "x", "share": Phone(made["code"]).share})
        self.assertEqual(status, 429)
        with unittest.mock.patch.object(brain.Handler, "caller_address", lambda handler: "172.31.255.9"):
            self.assertEqual(self.link(made)[0], 200)

    def test_a_bad_access_pair_is_a_400(self):
        status, _ = self.post("/v1/phone-link/offers", {"access": {"id": "only"}}, authorised=True)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
