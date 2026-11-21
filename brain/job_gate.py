import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_LOCK_FILE = "/root/gpu-lock"
DEFAULT_GPU_HOURS = (9, 17)
DEFAULT_QUIET_HOURS = (22, 8)
HOURS_IN_DAY = 24


def hour_pair(value, default: tuple[int, int]) -> tuple[int, int]:
    valid = (isinstance(value, (list, tuple)) and len(value) == 2
             and all(isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= HOURS_IN_DAY for hour in value))
    return (value[0], value[1]) if valid else default


def within_hours(hours: tuple[int, int], now: datetime) -> bool:
    start, end = hours
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


def lock_is_held(path: Path) -> bool:
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


@dataclass(frozen=True)
class GpuGate:
    lock_file: Path = Path(DEFAULT_LOCK_FILE)
    hours: tuple[int, int] = DEFAULT_GPU_HOURS

    @classmethod
    def from_settings(cls, settings: dict) -> "GpuGate":
        return cls(Path(str(settings.get("gpu_lock") or DEFAULT_LOCK_FILE)), hour_pair(settings.get("gpu_hours"), DEFAULT_GPU_HOURS))

    def refusal(self, now: datetime, explicit_timed: bool) -> str | None:
        if lock_is_held(self.lock_file):
            return "the GPU is reserved for other work right now"
        if not explicit_timed and not within_hours(self.hours, now):
            return f"the GPU is left asleep outside {self.hours[0]:02d}:00 to {self.hours[1]:02d}:00 unless you asked for this at a set time"
        return None


def quiet_now(settings: dict, now: datetime) -> bool:
    return within_hours(hour_pair(settings.get("quiet_hours"), DEFAULT_QUIET_HOURS), now)
