from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN_COPY = ROOT / "brain" / "tests" / "fixtures" / "phone_link_vectors.json"
PHONE_COPIES = (
    ROOT / "apps" / "ios" / "tests" / "fixtures" / "phone_link_vectors.json",
    ROOT / "apps" / "android" / "core" / "src" / "test" / "resources" / "phone_link_vectors.json",
)


def test_both_phone_apps_check_their_link_sums_against_the_brains_own_values():
    for copy in PHONE_COPIES:
        assert copy.read_bytes() == BRAIN_COPY.read_bytes(), copy
