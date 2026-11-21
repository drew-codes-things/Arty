import json
import os
from pathlib import Path

import file_reading
import media

SOUND_PYTHON = os.environ.get("WHISPER_PYTHON", "/srv/ai/media/venv/bin/python")
SOUND_SCRIPT = Path(__file__).with_name("sound_features.py")
SOUND_TIMEOUT_SECONDS = 300
CYLINDER_COUNTS = (3, 4, 6, 8)
PLAUSIBLE_RPM = (600, 7000)
IDLE_RPM = (600, 1100)
MIN_ENGINE_HZ = 5
MAX_ENGINE_HZ = 400
LOUD_CLIPPING_PERCENT = 0.5
QUIET_PEAK_DB = -30.0
LOW_BAND_SHARE = 0.6
LISTENED_SECONDS = 30
HOW_TO_ANSWER = (
    "How to answer from this: say in plain words what the recording shows, using the facts above. Give possible causes only as "
    "possibilities, each with how sure you are (low or medium, never certain), and say what else would be needed to tell them "
    "apart (a closer, steadier recording; the engine speed; cold or warm; when it happens). A phone recording cannot show that a "
    "part is faulty or fine: the microphone, wind, distance, cabin noise, automatic gain and where it was held all change the "
    "sound. Do not say it is fine, and do not say what is broken. Say a mechanic should listen in person, and say it firmly for "
    "an engine knock or tapping, grinding or squealing brakes, a noise that is loud, gets worse or changes with steering, "
    "braking or speed, or anything touching safety.")


def run_features(path: str, start: float = 0.0) -> dict:
    result = media._run(["nice", "-n", "10", SOUND_PYTHON, str(SOUND_SCRIPT), path, f"{start:.2f}"], SOUND_TIMEOUT_SECONDS, "Analysing the sound")
    try:
        facts = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise media.MediaError("the sound analysis gave no result") from None
    if "error" in facts:
        raise media.MediaError(facts["error"])
    return facts


def level_lines(facts: dict) -> list[str]:
    level, loudness = facts["level"], facts["loudness"]
    trend = {"steady": "steady", "rising": "getting louder", "falling": "getting quieter", "varying": "going up and down"}[loudness["trend"]]
    timeline = ", ".join(f"{moment:g}s {value:g}" for moment, value in loudness["points"])
    lines = [f"Analysed {facts['seconds']:g} seconds. Average level {level['rms_db']:g} dB, loudest {level['peak_db']:g} dB (0 dB is the loudest "
             f"a recording can be). Loudness is {trend} (varies by {loudness['range_db']:g} dB). Level over time, in dB: {timeline}."]
    if level["silent_percent"] >= 50:
        lines.append(f"About {level['silent_percent']}% of it is near silence.")
    return lines


def frequency_lines(facts: dict) -> list[str]:
    spectrum = facts["spectrum"]
    if not spectrum["bands"]:
        return ["No clear frequencies: it is too quiet to measure."]
    if not spectrum["peaks"]:
        shares = ", ".join(f"{share * 100:.0f}% at {band.replace('-', ' to ')} Hz" for band, share in spectrum["bands"].items())
        return [f"No single frequency stands out: it is spread across the spectrum like noise. Where the sound energy is: {shares}."]
    peaks = ", ".join(f"{peak['hz']:g} Hz ({peak['relative_db']:g} dB)" for peak in spectrum["peaks"])
    shares = ", ".join(f"{share * 100:.0f}% at {band.replace('-', ' to ')} Hz" for band, share in spectrum["bands"].items())
    lines = [f"Strongest frequencies (level against the strongest): {peaks}. Where the sound energy is: {shares}. Average pitch "
             f"(centre of the sound) {spectrum['centroid_hz']} Hz."]
    if spectrum["fundamental_hz"]:
        lines.append(f"Several strong frequencies are multiples of {spectrum['fundamental_hz']:g} Hz, so there is a steady tone with "
                     "harmonics at that pitch (something rotating or firing at that rate).")
    return lines


def rhythm_lines(facts: dict) -> list[str]:
    rhythm = facts["rhythm"]
    if rhythm is None:
        return ["No clear repeating rhythm in the loudness."]
    return [f"The loudness repeats about {rhythm['rate_hz']:g} times a second (every {rhythm['period_s'] * 1000:.0f} ms), "
            f"{rhythm['strength']:.2f} out of 1 clear, about {rhythm['repeats']} repeats in this recording."]


def impulse_lines(facts: dict) -> list[str]:
    impulses = facts["impulses"]
    if impulses["count"] == 0:
        return ["No separate clicks, knocks or taps found."]
    kinds = ", ".join(f"{count} {kind}{'s' if count != 1 else ''}" for kind, count in impulses["kinds"].items())
    line = f"Separate sharp sounds (clicks, knocks, taps): {impulses['count']} found, {impulses['per_second']:g} a second ({kinds})."
    if "spread" in impulses:
        line += f" Typical gap {impulses['median_interval_s'] * 1000:.0f} ms, {impulses['spread']}."
    times = ", ".join(f"{event['t']:g}s ({event['kind']}, {event['db']:g} dB)" for event in impulses["listed"])
    return [line, f"Some of them: {times}."]


def tone_lines(facts: dict) -> list[str]:
    lines = []
    for tone in facts["tones"]:
        kind = "squeal or whistle" if tone["hz"] >= 1500 else "whine or hum"
        glide = f", rising by {tone['glide_hz']} Hz" if tone["glide_hz"] > 0 else f", falling by {-tone['glide_hz']} Hz" if tone["glide_hz"] < 0 else ", steady"
        lines.append(f"A {kind}: a single pure tone around {tone['hz']} Hz from {tone['start_s']:g}s for {tone['seconds']:g}s{glide}.")
    return lines or ["No steady squeal, whistle or whine."]


def quality_lines(facts: dict) -> list[str]:
    level, spectrum = facts["level"], facts["spectrum"]
    lines = []
    if level["clipped_percent"] >= LOUD_CLIPPING_PERCENT:
        lines.append(f"Recording quality: it is clipping ({level['clipped_percent']:g}% of samples at the limit), so the sound is distorted.")
    if level["peak_db"] < QUIET_PEAK_DB:
        lines.append("Recording quality: it is very quiet, so quiet details may be lost.")
    if spectrum["bands"] and spectrum["bands"].get("20-200", 0) >= LOW_BAND_SHARE:
        lines.append("Recording quality: most of the energy is below 200 Hz, which wind, handling or a phone's gain also cause.")
    return lines


def rpm_for(rate_hz: float, events_per_two_turns: float) -> int:
    return round(rate_hz * 120 / events_per_two_turns)


def engine_speed_lines(facts: dict) -> list[str]:
    rates = []
    if facts["rhythm"] and MIN_ENGINE_HZ <= facts["rhythm"]["rate_hz"] <= MAX_ENGINE_HZ:
        rates.append(("repeating beat", facts["rhythm"]["rate_hz"]))
    if facts["spectrum"]["fundamental_hz"]:
        rates.append(("steady tone", facts["spectrum"]["fundamental_hz"]))
    lines = []
    low, high = PLAUSIBLE_RPM
    for name, rate in rates:
        fits = [f"{cylinders} cylinders: {rpm_for(rate, cylinders)} rpm" for cylinders in CYLINDER_COUNTS if low <= rpm_for(rate, cylinders) <= high]
        once_a_turn, once_in_two = round(rate * 60), round(rate * 120)
        turn_fits = [text for text, speed in ((f"once every turn: {once_a_turn} rpm", once_a_turn), (f"once every two turns: {once_in_two} rpm", once_in_two))
                     if low <= speed <= high]
        if fits:
            lines.append(f"If the {name} ({rate:g} Hz) is a four-stroke engine firing, it would mean {'; '.join(fits)}. Idle is usually "
                         f"{IDLE_RPM[0]} to {IDLE_RPM[1]} rpm.")
        if turn_fits:
            lines.append(f"If the {name} is a part that repeats with the engine, it would mean {'; '.join(turn_fits)}.")
    if lines:
        lines.append("These engine-speed figures are arithmetic from the rate, not a measurement: the real engine speed (a rev counter) "
                     "is needed to know which, if any, applies.")
    return lines


def described(facts: dict) -> str:
    sections = [level_lines(facts), frequency_lines(facts), rhythm_lines(facts), impulse_lines(facts), tone_lines(facts),
                engine_speed_lines(facts), quality_lines(facts)]
    return "\n".join(line for section in sections for line in section)


def report(path: str, start: float = 0.0, heard: str = "") -> str:
    text = "Measured from the recording (numbers worked out by a program, not guessed):\n" + described(run_features(path, start))
    return text + (f"\n\nWhat a listening model said it heard (it can be wrong):\n{file_reading.marked(heard)}" if heard else "") + f"\n\n{HOW_TO_ANSWER}"
