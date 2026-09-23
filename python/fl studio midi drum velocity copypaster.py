from __future__ import annotations

import enum
import os
import shutil
import sys
import tempfile
import warnings
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

try:
    import pyflp
    from pyflp.arrangement import PatternPLItem
except ImportError:
    print("PyFLP is not installed yet. Run this once, then start me again:\n")
    print('    python -m pip install '
          '"git+https://github.com/Meowrium/PyFLP.git@9cae5fdfeb801629a5dbc12690af7c80c3ebac15"')
    sys.exit(1)


def let_pyflp_enums_resolve_unknown_ids() -> None:
    original_call = enum.EnumMeta.__call__

    def patched_call(cls, value=None, *args, **kwargs):
        if (cls.__module__.startswith("pyflp.") and not cls._member_map_
                and not args and not kwargs and isinstance(value, int)):
            member = cls._missing_(value)
            if member is not None:
                return member
        return original_call(cls, value, *args, **kwargs)

    enum.EnumMeta.__call__ = patched_call


let_pyflp_enums_resolve_unknown_ids()


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

DRUM_NAME_BY_KEY = {
    "D#2": "High Q", "E2": "Slap", "F2": "Scratch Push", "F#2": "Scratch Pull",
    "G2": "Sticks", "G#2": "Square Click", "A2": "Metronome Click",
    "A#2": "Metronome Bell", "B2": "Acoustic Bass Drum", "C3": "Bass Drum 1",
    "C#3": "Side Stick", "D3": "Acoustic Snare", "D#3": "Hand Clap",
    "E3": "Electric Snare", "F3": "Low Floor Tom", "F#3": "Closed Hi-Hat",
    "G3": "High Floor Tom", "G#3": "Pedal Hi-Hat", "A3": "Low Tom",
    "A#3": "Open Hi-Hat", "B3": "Low-Mid Tom", "C4": "Hi-Mid Tom",
    "C#4": "Crash Cymbal 1", "D4": "High Tom", "D#4": "Ride Cymbal 1",
    "E4": "Chinese Cymbal", "F4": "Ride Bell", "F#4": "Tambourine",
    "G4": "Splash Cymbal", "G#4": "Cowbell", "A4": "Crash Cymbal 2",
    "A#4": "Vibraslap", "B4": "Ride Cymbal 2", "C5": "Hi Bongo",
    "C#5": "Low Bongo", "D5": "Mute Hi Conga", "D#5": "Open Hi Conga",
    "E5": "Low Conga", "F5": "High Timbale", "F#5": "Low Timbale",
    "G5": "High Agogo", "G#5": "Low Agogo", "A5": "Cabasa", "A#5": "Maracas",
    "B5": "Short Whistle", "C6": "Long Whistle", "C#6": "Short Guiro",
    "D6": "Long Guiro", "D#6": "Claves", "E6": "Hi Wood Block",
    "F6": "Low Wood Block", "F#6": "Mute Cuica", "G6": "Open Cuica",
    "G#6": "Mute Triangle", "A6": "Open Triangle",
}

FLP_DATA_SIZE_AT = 18
FLP_HEADER_SIZE = 22
EVENT_WORD, EVENT_DWORD, EVENT_TEXT = 64, 128, 192
EVENT_NEW_PATTERN = 65
EVENT_FL26_VARINT_VALUE = 0xAC
EVENT_FL_VERSION = 199
EVENT_PATTERN_NOTES = 224
NOTE_SIZE = 24
NOTE_CHANNEL_OFFSET = 6
NOTE_KEY_OFFSET = 12
NOTE_VELOCITY_OFFSET = 21


class Stop(Exception):
    pass


def labelled(kind: str, number: int, name: str | None) -> str:
    return f"{kind} {number}: {name}" if name else f"{kind} {number}"


def counted(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def key_label(key: int) -> str:
    key_name = NOTE_NAMES[key % 12] + str(key // 12)
    drum_name = DRUM_NAME_BY_KEY.get(key_name)
    return f"{key_name} {drum_name}" if drum_name else key_name


@dataclass(frozen=True)
class Event:
    id: int
    start: int
    data_at: int
    end: int
    fl26_varint_value: bool


@dataclass(frozen=True)
class Note:
    offset: int
    key: int
    channel: int
    velocity: int


@dataclass
class Pattern:
    iid: int
    label: str
    notes: list[Note]


@dataclass
class Track:
    label: str
    pattern_iids: list[int]


@dataclass
class VelocityChange:
    pattern: Pattern
    note: Note
    new_velocity: int


@dataclass
class Plan:
    source: Pattern
    channel_label: str
    velocity_counts_by_key: dict[int, Counter[int]]
    velocity_by_key: dict[int, int]
    targets: list[Pattern]
    changes: list[VelocityChange]
    missing_patterns_by_key: dict[int, list[str]]
    skipped_patterns_by_key: dict[int, list[str]]


class FlStudioProject:
    def __init__(self, path: Path, raw: bytes, notes_by_pattern: dict[int, list[Note]],
                 patterns: dict[int, Pattern], tracks: list[Track],
                 channel_labels: dict[int, str]) -> None:
        self._path = path
        self.tracks = tracks
        self._raw = raw
        self._notes_by_pattern = notes_by_pattern
        self._patterns = patterns
        self._channel_labels = channel_labels

    @classmethod
    def open(cls, path: Path) -> FlStudioProject:
        raw = path.read_bytes()
        notes_by_pattern = cls._scan_notes(raw)
        parsed = cls._parse_with_pyflp(raw)
        cls._check_notes_agree(parsed, notes_by_pattern)
        patterns = {
            pattern.iid: Pattern(pattern.iid, labelled("Pattern", pattern.iid, pattern.name),
                                 notes_by_pattern.get(pattern.iid, []))
            for pattern in parsed.patterns
        }
        channel_labels = {
            channel.iid: labelled("Channel", channel.iid, channel.display_name)
            for channel in parsed.channels
        }
        return cls(path, raw, notes_by_pattern, patterns, cls._read_tracks(parsed),
                   channel_labels)

    def patterns_on(self, track: Track) -> list[Pattern]:
        return [self._patterns[iid] for iid in track.pattern_iids]

    def channel_label(self, channel: int) -> str:
        return self._channel_labels.get(channel, labelled("Channel", channel, None))

    def write_candidate(self, changes: list[VelocityChange]) -> Path:
        patched = bytearray(self._raw)
        for change in changes:
            patched[change.note.offset + NOTE_VELOCITY_OFFSET] = change.new_velocity
        candidate = self._path.with_name(f"{self._path.stem}.velocity_copy_tmp.flp")
        candidate.write_bytes(patched)
        try:
            self._verify_candidate(candidate, changes)
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        return candidate

    def commit(self, candidate: Path) -> Path:
        if self._path.read_bytes() != self._raw:
            raise Stop("The project file was changed on disk while I was running "
                       "(saved from FL Studio?). Nothing was changed - please start again.")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup = self._path.with_suffix(f".backup_{timestamp}.flp")
        shutil.copy2(self._path, backup)
        os.replace(candidate, self._path)
        return backup

    def _verify_candidate(self, candidate: Path, changes: list[VelocityChange]) -> None:
        new_velocity_at = {change.note.offset: change.new_velocity for change in changes}
        patched = candidate.read_bytes()
        changed_offsets = {i for i, (a, b) in enumerate(zip(self._raw, patched)) if a != b}
        expected_offsets = {offset + NOTE_VELOCITY_OFFSET for offset in new_velocity_at}
        if len(patched) != len(self._raw) or changed_offsets != expected_offsets:
            raise Stop("Self-check failed: the prepared file differs from the plan. "
                       "Nothing was changed.")
        expected_notes = {
            iid: [replace(note, velocity=new_velocity_at.get(note.offset, note.velocity))
                  for note in notes]
            for iid, notes in self._notes_by_pattern.items()
        }
        self._check_notes_agree(self._parse_with_pyflp(patched), expected_notes)

    @staticmethod
    def _parse_with_pyflp(raw: bytes):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.flp"
            path.write_bytes(FlStudioProject._pyflp_readable(raw))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return pyflp.parse(str(path))

    @staticmethod
    def _pyflp_readable(raw: bytes) -> bytes:
        events = bytearray()
        for event in FlStudioProject._events(raw):
            if event.fl26_varint_value:
                events += bytes([event.id, event.end - event.data_at])
                events += raw[event.data_at:event.end]
            else:
                events += raw[event.start:event.end]
        return (raw[:FLP_DATA_SIZE_AT] + len(events).to_bytes(4, "little")
                + bytes(events))

    @staticmethod
    def _check_notes_agree(parsed, notes_by_pattern: dict[int, list[Note]]) -> None:
        for pattern in parsed.patterns:
            expected = [(note["key"], note.rack_channel, note.velocity) for note in pattern.notes]
            found = [(note.key, note.channel, note.velocity)
                     for note in notes_by_pattern.get(pattern.iid, [])]
            if found != expected:
                raise Stop(f"I can't read the notes of pattern {pattern.iid} reliably, "
                           "so I won't touch this file. Nothing was changed.")

    @staticmethod
    def _read_tracks(parsed) -> list[Track]:
        arrangements = list(parsed.arrangements)
        tracks = []
        for arrangement in arrangements:
            for track in arrangement.tracks:
                pattern_iids = list(dict.fromkeys(
                    clip.pattern.iid for clip in track if isinstance(clip, PatternPLItem)))
                if not pattern_iids:
                    continue
                label = labelled("Track", track.iid, track.name)
                if len(arrangements) > 1:
                    arrangement_label = labelled("Arrangement", arrangement.iid, arrangement.name)
                    label = f"{arrangement_label} / {label}"
                tracks.append(Track(label, pattern_iids))
        return tracks

    @staticmethod
    def _scan_notes(raw: bytes) -> dict[int, list[Note]]:
        current_pattern = None
        notes_by_pattern: dict[int, list[Note]] = {}
        for event in FlStudioProject._events(raw):
            if event.id == EVENT_NEW_PATTERN:
                current_pattern = int.from_bytes(raw[event.data_at:event.end], "little")
            elif event.id == EVENT_PATTERN_NOTES:
                if current_pattern is None or (event.end - event.data_at) % NOTE_SIZE:
                    raise Stop("A pattern's notes are laid out in a way I don't recognise. "
                               "Nothing was changed.")
                notes_by_pattern.setdefault(current_pattern, []).extend(
                    FlStudioProject._read_note(raw, offset)
                    for offset in range(event.data_at, event.end, NOTE_SIZE))
        return notes_by_pattern

    @staticmethod
    def _events(raw: bytes):
        if (raw[0:4] != b"FLhd" or raw[14:18] != b"FLdt"
                or int.from_bytes(raw[FLP_DATA_SIZE_AT:FLP_HEADER_SIZE], "little")
                != len(raw) - FLP_HEADER_SIZE):
            raise Stop("This doesn't look like a valid FL Studio project file.")
        position = FLP_HEADER_SIZE
        fl_major_version = None
        while position < len(raw):
            start = position
            event_id = raw[position]
            position += 1
            if fl_major_version is None and event_id != EVENT_FL_VERSION:
                raise Stop("The FL Studio version is missing from the file, so I can't "
                           "read it safely. Nothing was changed.")
            fl26_varint_value = event_id == EVENT_FL26_VARINT_VALUE and fl_major_version >= 26
            data_at = position
            if fl26_varint_value:
                _, position = FlStudioProject._read_varint(raw, position)
            elif event_id < EVENT_WORD:
                position += 1
            elif event_id < EVENT_DWORD:
                position += 2
            elif event_id < EVENT_TEXT:
                position += 4
            else:
                size, data_at = FlStudioProject._read_varint(raw, position)
                position = data_at + size
            if position > len(raw):
                raise Stop("The project file ends unexpectedly. Nothing was changed.")
            if event_id == EVENT_FL_VERSION:
                fl_major_version = FlStudioProject._read_major_version(raw[data_at:position])
            yield Event(event_id, start, data_at, position, fl26_varint_value)

    @staticmethod
    def _read_varint(raw: bytes, position: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            if position >= len(raw):
                raise Stop("The project file ends unexpectedly. Nothing was changed.")
            byte = raw[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, position
            shift += 7

    @staticmethod
    def _read_major_version(data: bytes) -> int:
        major = data.decode("ascii", errors="replace").rstrip("\0").split(".")[0]
        if not major.isdigit():
            raise Stop(f"I can't read the FL Studio version ({major!r}), so I won't "
                       "touch this file. Nothing was changed.")
        return int(major)

    @staticmethod
    def _read_note(raw: bytes, offset: int) -> Note:
        return Note(
            offset=offset,
            key=int.from_bytes(
                raw[offset + NOTE_KEY_OFFSET:offset + NOTE_KEY_OFFSET + 2], "little"),
            channel=int.from_bytes(
                raw[offset + NOTE_CHANNEL_OFFSET:offset + NOTE_CHANNEL_OFFSET + 2], "little"),
            velocity=raw[offset + NOTE_VELOCITY_OFFSET],
        )


def ask(question: str) -> str:
    answer = input(question).strip().strip('"').strip("'")
    if not answer:
        raise Stop("Nothing entered - leaving without changing anything.")
    return answer


def ask_choice(heading: str, prompt: str, labels: list[str]) -> int:
    print(heading)
    for number, label in enumerate(labels, start=1):
        print(f"  {number}. {label}")
    while True:
        choice = ask(prompt)
        if choice.isdigit() and 1 <= int(choice) <= len(labels):
            print()
            return int(choice) - 1
        print(f"  Please enter a number between 1 and {len(labels)}.\n")


def show_intro() -> None:
    print("=== Drum velocity copier ===")
    print("I copy the velocities of one channel from one corrected pattern onto the")
    print("other patterns on the same playlist track, and even out that channel in the")
    print("corrected pattern itself if you ask me to. Leave any answer blank to quit.")
    print("Don't save the project from FL Studio while I'm running.\n")


def ask_for_project() -> FlStudioProject:
    print("Step 1 of 5  -  Which FL Studio project?")
    while True:
        path = Path(ask("  Drag the .flp file here (or type its path): "))
        if not path.is_file():
            print("  I can't find that file. Try again.\n")
            continue
        try:
            project = FlStudioProject.open(path)
        except Exception as error:
            print(f"  I couldn't open that project: {error}\n")
            continue
        print(f"  Opened: {path.name}\n")
        return project


def ask_for_track(project: FlStudioProject) -> Track:
    if not project.tracks:
        raise Stop("This project has no playlist tracks with patterns on them.")
    index = ask_choice("Step 2 of 5  -  Which playlist track holds the drum patterns?",
                       "  Enter the number of the drum track: ",
                       [track.label for track in project.tracks])
    return project.tracks[index]


def ask_for_source_pattern(project: FlStudioProject, track: Track) -> Pattern:
    patterns = project.patterns_on(track)
    index = ask_choice("Step 3 of 5  -  Which pattern did you already correct?",
                       "  Enter the number of your corrected pattern: ",
                       [pattern.label for pattern in patterns])
    return patterns[index]


def ask_for_channel(project: FlStudioProject, source: Pattern) -> int:
    channels = sorted({note.channel for note in source.notes})
    if not channels:
        raise Stop("That pattern has no notes, so there is nothing to copy.")
    index = ask_choice("Step 4 of 5  -  Which channel's velocities should be copied?",
                       "  Enter the number of the channel: ",
                       [project.channel_label(channel) for channel in channels])
    return channels[index]


def other_patterns_on_track(project: FlStudioProject, track: Track,
                            source: Pattern) -> list[Pattern]:
    targets = [pattern for pattern in project.patterns_on(track) if pattern.iid != source.iid]
    if not targets:
        raise Stop("There are no other patterns on that track to change.")
    return targets


def source_velocity_counts(source: Pattern, channel: int) -> dict[int, Counter[int]]:
    counts_by_key: dict[int, Counter[int]] = {}
    for note in source.notes:
        if note.channel == channel:
            counts_by_key.setdefault(note.key, Counter())[note.velocity] += 1
    return counts_by_key


def describe_counts(counts: Counter[int]) -> str:
    return ", ".join(f"{velocity} x{count}" for velocity, count in counts.most_common())


def ask_for_velocity_choices(counts_by_key: dict[int, Counter[int]]) -> dict[int, int]:
    conflicting_keys = sorted(key for key, counts in counts_by_key.items() if len(counts) > 1)
    print("Step 5 of 5  -  Keys with several velocities in your corrected pattern")
    if not conflicting_keys:
        print("  None - nothing to choose.\n")
    chosen_velocity_by_key = {}
    for key in conflicting_keys:
        options = counts_by_key[key].most_common()
        labels = [f"Use {velocity} ({counted(count, 'note')})" for velocity, count in options]
        index = ask_choice(f"  {key_label(key)}:", "  Enter your choice: ",
                           labels + ["Don't overwrite this key"])
        if index < len(options):
            chosen_velocity_by_key[key] = options[index][0]
    return chosen_velocity_by_key


def velocity_changes(pattern: Pattern, channel: int,
                     velocity_by_key: dict[int, int]) -> list[VelocityChange]:
    return [VelocityChange(pattern, note, velocity_by_key[note.key])
            for note in pattern.notes
            if note.channel == channel and note.key in velocity_by_key
            and note.velocity != velocity_by_key[note.key]]


def untouched_patterns_by_key(targets: list[Pattern], channel: int,
                              velocity_by_key: dict[int, int]) -> dict[int, list[str]]:
    patterns_by_key: dict[int, list[str]] = {}
    for pattern in targets:
        for note in pattern.notes:
            if note.channel == channel and note.key not in velocity_by_key:
                labels = patterns_by_key.setdefault(note.key, [])
                if pattern.label not in labels:
                    labels.append(pattern.label)
    return patterns_by_key


def plan_velocity_copy(source: Pattern, targets: list[Pattern], channel: int,
                       channel_label: str, velocity_counts_by_key: dict[int, Counter[int]],
                       chosen_velocity_by_key: dict[int, int]) -> Plan:
    velocity_by_key = {key: next(iter(counts)) for key, counts in velocity_counts_by_key.items()
                       if len(counts) == 1}
    velocity_by_key.update(chosen_velocity_by_key)
    changes = [change for pattern in [source, *targets]
               for change in velocity_changes(pattern, channel, velocity_by_key)]
    untouched = untouched_patterns_by_key(targets, channel, velocity_by_key)
    missing = {key: labels for key, labels in untouched.items()
               if key not in velocity_counts_by_key}
    skipped = {key: labels for key, labels in untouched.items()
               if key in velocity_counts_by_key}
    return Plan(source, channel_label, velocity_counts_by_key,
                velocity_by_key, targets, changes, missing, skipped)


def show_plan(plan: Plan) -> None:
    show_source_velocities(plan)
    show_changes_per_pattern(plan)
    show_untouched_keys("Left untouched - not in your corrected pattern:",
                        plan.missing_patterns_by_key)
    show_untouched_keys("Left untouched - you chose not to overwrite:",
                        plan.skipped_patterns_by_key)
    changed_patterns = len({change.pattern.iid for change in plan.changes})
    print(f"\nIn total {counted(len(plan.changes), 'note')} in "
          f"{counted(changed_patterns, 'pattern')} will change.\n")


def show_source_velocities(plan: Plan) -> None:
    print(f"Source: {plan.source.label}, {plan.channel_label}")
    for key in sorted(plan.velocity_counts_by_key):
        counts = plan.velocity_counts_by_key[key]
        if len(counts) == 1:
            print(f"  {key_label(key):<24} {plan.velocity_by_key[key]}")
        elif key in plan.velocity_by_key:
            print(f"  {key_label(key):<24} {plan.velocity_by_key[key]}"
                  f"  (your choice from {describe_counts(counts)})")
        else:
            print(f"  {key_label(key):<24} not overwritten ({describe_counts(counts)})")


def show_changes_per_pattern(plan: Plan) -> None:
    print("\nPlanned changes:")
    for pattern in [plan.source, *plan.targets]:
        rows = Counter((change.note.key, change.note.velocity, change.new_velocity)
                       for change in plan.changes if change.pattern is pattern)
        label = f"{pattern.label} (source)" if pattern is plan.source else pattern.label
        if not rows:
            print(f"\n{label}: no changes")
            continue
        print(f"\n{label}:")
        print(f"  {'Key':<24} {'Before':>6} {'After':>6} {'Notes':>6}")
        for (key, before, after), count in sorted(rows.items()):
            print(f"  {key_label(key):<24} {before:>6} {after:>6} {count:>6}")


def show_untouched_keys(heading: str, patterns_by_key: dict[int, list[str]]) -> None:
    if patterns_by_key:
        print(f"\n{heading}")
        for key in sorted(patterns_by_key):
            print(f"  {key_label(key)}: {', '.join(patterns_by_key[key])}")


def stop_if_nothing_to_change(plan: Plan) -> None:
    if not plan.changes:
        input("No changes needed - all velocities already match, so there is nothing "
              "to apply. Press Enter to exit.")
        raise Stop("The project file was left as it is.")


def apply_if_confirmed(project: FlStudioProject, plan: Plan) -> None:
    candidate = project.write_candidate(plan.changes)
    try:
        answer = ask("Apply these velocities and save? A backup is made first (yes/no): ")
        if answer.lower() not in {"yes", "y"}:
            print("Left unchanged.")
            return
        backup = project.commit(candidate)
        print(f"\nDone. Changed {counted(len(plan.changes), 'note')}.")
        print(f"Backup of the original: {backup.name}")
    finally:
        candidate.unlink(missing_ok=True)


def run() -> None:
    show_intro()
    project = ask_for_project()
    track = ask_for_track(project)
    source = ask_for_source_pattern(project, track)
    channel = ask_for_channel(project, source)
    targets = other_patterns_on_track(project, track, source)
    velocity_counts = source_velocity_counts(source, channel)
    chosen_velocities = ask_for_velocity_choices(velocity_counts)
    plan = plan_velocity_copy(source, targets, channel, project.channel_label(channel),
                              velocity_counts, chosen_velocities)
    show_plan(plan)
    stop_if_nothing_to_change(plan)
    apply_if_confirmed(project, plan)


def main() -> None:
    try:
        run()
    except Stop as stop:
        print(f"\n{stop}")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped - the project file was not changed.")
    except Exception as error:
        print(f"\nUnexpected error: {type(error).__name__}: {error}")
        print("The project file was not changed.")


if __name__ == "__main__":
    main()