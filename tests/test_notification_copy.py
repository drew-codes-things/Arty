import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_CATEGORIES = ROOT / "apps/android/core/src/main/kotlin/app/arty/core/NotificationCategory.kt"
IPHONE_CATEGORIES = ROOT / "apps/ios/js/notifications.js"
ANDROID_ENTRY = re.compile(r'^\s*\w+\(\s*"(?P<key>[a-z]+)",\s*"(?P<title>[^"]+)",\s*"(?P<description>[^"]+)"', re.MULTILINE)
IPHONE_ENTRY = re.compile(r'\{\s*key:\s*"(?P<key>[a-z]+)",\s*title:\s*"(?P<title>[^"]+)",\s*description:\s*"(?P<description>[^"]+)"')


def categories(pattern: re.Pattern, path: Path) -> list[tuple[str, str, str]]:
    return [(found["key"], found["title"], found["description"]) for found in pattern.finditer(path.read_text())]


def test_both_apps_name_and_describe_each_category_the_same_way():
    android = categories(ANDROID_ENTRY, ANDROID_CATEGORIES)
    assert len(android) == 9
    assert android == categories(IPHONE_ENTRY, IPHONE_CATEGORIES)
