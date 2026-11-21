import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_CAMERA = ROOT / "apps/desktop/server/process/tools/camera.py"
BRAIN_GUIDANCE = ROOT / "brain/look_guidance.py"
SHARED_NAMES = ("APPEARANCE_PATTERN", "APPEARANCE_GUIDANCE")
QUESTIONS = ("how do I look?", "Tell me how I look", "is my hair a mess", "how do I look up my order", "what colour is the mug")


def constants(path: Path) -> dict[str, str]:
    found = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in SHARED_NAMES:
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def test_the_webcam_and_the_phone_photo_path_use_the_same_appearance_guidance_and_pattern():
    desktop, brain = constants(DESKTOP_CAMERA), constants(BRAIN_GUIDANCE)
    assert sorted(desktop) == sorted(brain) == sorted(SHARED_NAMES)
    assert desktop == brain


def test_both_sides_tell_the_same_questions_apart():
    pattern = re.compile(constants(BRAIN_GUIDANCE)["APPEARANCE_PATTERN"], re.IGNORECASE)
    assert [bool(pattern.search(question)) for question in QUESTIONS] == [True, True, True, False, False]
