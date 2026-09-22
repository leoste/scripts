"""Copy correct drum velocities onto every drum clip on a playlist track.

Just run it - it asks for everything it needs, one step at a time:

    python drum_velocity_copier.py

First-time setup (once): FL Studio 24/25/26 files need a specific PyFLP build:

    python -m pip install "git+https://github.com/Meowrium/PyFLP.git"

Works on any recent Python: the compatibility fix below lets that build (written
for Python 3.10) run on Python 3.11 and 3.12 as well.

The file is one story told top to bottom, in three sections:

    1. Drum names        turn a piano-roll key into a readable drum name
    2. FlStudioProject   the only part that talks to the FL Studio file
    3. The conversation  the step-by-step questions, report, and apply
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import pyflp
    from pyflp.arrangement import PatternPLItem
except ImportError:
    print("PyFLP is not installed yet. Run this once, then start me again:\n")
    print('    python -m pip install "git+https://github.com/Meowrium/PyFLP.git"')
    sys.exit(1)


# --- Compatibility fix for Python 3.11+ ------------------------------------
# PyFLP resolves an event ID by calling a member-less base enum, e.g.
# EventEnum(213). Python 3.11+ refuses to call an enum that has no members
# before its _missing_ hook runs - but that hook is exactly how PyFLP turns the
# number into an ID. We patch the enum metaclass so that a member-less enum,
# called with an integer, is routed to its _missing_ hook, restoring the
# behaviour PyFLP was written for. Enums that DO have members are untouched, so
# this is safe, and it is a no-op on Python 3.10.
import enum as _enum


def _make_pyflp_work_on_new_python() -> None:
    metaclass = _enum.EnumMeta  # also called enum.EnumType on Python 3.11+
    if getattr(metaclass, "_flp_call_patched", False):
        return
    original_call = metaclass.__call__

    def patched_call(cls, value=None, *args, **kwargs):
        member_map = getattr(cls, "_member_map_", None)
        if not member_map and not args and not kwargs and isinstance(value, int):
            missing = getattr(cls, "_missing_", None)
            if missing is not None:
                member = missing(value)
                if member is not None:
                    return member
        return original_call(cls, value, *args, **kwargs)

    metaclass.__call__ = patched_call
    metaclass._flp_call_patched = True


_make_pyflp_work_on_new_python()


# ======================================================================
# 1. Drum names
#    Turn a piano-roll key (like "F#3") into a drum name ("Closed Hi-Hat").
#    FL Studio shows these General MIDI names for a MIDI-out drum channel.
#    They are only for readable output - the program matches drums by their
#    key, so a missing name breaks nothing (you just see the raw key).
# ======================================================================

# The order here is the order drums sit on the keyboard, low to high.
DRUM_NAME_BY_KEY = {
    "D#2": "High Q",
    "E2": "Slap",
    "F2": "Scratch Push",
    "F#2": "Scratch Pull",
    "G2": "Sticks",
    "G#2": "Square Click",
    "A2": "Metronome Click",
    "A#2": "Metronome Bell",
    "B2": "Acoustic Bass Drum",
    "C3": "Bass Drum 1",
    "C#3": "Side Stick",
    "D3": "Acoustic Snare",
    "D#3": "Hand Clap",
    "E3": "Electric Snare",
    "F3": "Low Floor Tom",
    "F#3": "Closed Hi-Hat",
    "G3": "High Floor Tom",
    "G#3": "Pedal Hi-Hat",
    "A3": "Low Tom",
    "A#3": "Open Hi-Hat",
    "B3": "Low-Mid Tom",
    "C4": "Hi-Mid Tom",
    "C#4": "Crash Cymbal 1",
    "D4": "High Tom",
    "D#4": "Ride Cymbal 1",
    "E4": "Chinese Cymbal",
    "F4": "Ride Bell",
    "F#4": "Tambourine",
    "G4": "Splash Cymbal",
    "G#4": "Cowbell",
    "A4": "Crash Cymbal 2",
    "A#4": "Vibraslap",
    "B4": "Ride Cymbal 2",
    "C5": "Hi Bongo",
    "C#5": "Low Bongo",
    "D5": "Mute Hi Conga",
    "D#5": "Open Hi Conga",
    "E5": "Low Conga",
    "F5": "High Timbale",
    "F#5": "Low Timbale",
    "G5": "High Agogo",
    "G#5": "Low Agogo",
    "A5": "Cabasa",
    "A#5": "Maracas",
    "B5": "Short Whistle",
    "C6": "Long Whistle",
    "C#6": "Short Guiro",
    "D6": "Long Guiro",
    "D#6": "Claves",
    "E6": "Hi Wood Block",
    "F6": "Low Wood Block",
    "F#6": "Mute Cuica",
    "G6": "Open Cuica",
    "G#6": "Mute Triangle",
    "A6": "Open Triangle",
}

_KEYBOARD_ORDER = list(DRUM_NAME_BY_KEY)


def name_of_drum(key: str) -> str:
    """The drum's name, or the raw key if it isn't a known drum."""
    return DRUM_NAME_BY_KEY.get(key, key)


def keyboard_position_of_drum(key: str) -> int:
    """Used to list drums low-to-high; unknown drums go last."""
    return _KEYBOARD_ORDER.index(key) if key in DRUM_NAME_BY_KEY else len(_KEYBOARD_ORDER)


# ======================================================================
# 2. FL Studio file access
#    The only part that talks to PyFLP. If the library ever changes, this
#    class is the only place to look; everything else works with plain
#    strings and numbers.
# ======================================================================

def _same_name(a: str | None, b: str) -> bool:
    """Compare names ignoring case and stray spaces (and handle unnamed items)."""
    return a is not None and a.strip().casefold() == b.strip().casefold()


# --- Low-level velocity patching -------------------------------------------
# We never let PyFLP re-save the project: on FL Studio 24+/26 files its writer
# rewrites almost the whole file and the result won't open in FL Studio.
# Instead we change ONLY the velocity byte of each affected note directly in the
# original file's bytes, leaving every other byte untouched - exactly what FL
# Studio itself does when you edit a velocity, so the file stays valid.
#
# FLP layout: a 22-byte header, then events as <1-byte id><value>. The value
# length depends on the id:
#     id < 64 -> 1 byte     64..127 -> 2 bytes     128..191 -> 4 bytes
#     id >= 192 (and the FL26 exception 0xAC) -> a varint length, then the data.
# A note is 24 bytes; its velocity is byte 21 and its key is a uint16 at byte 12.
_EVENT_WORD, _EVENT_DWORD, _EVENT_TEXT = 64, 128, 192
_NOTES_EVENT = 224          # the block holding a pattern's notes
_NEW_PATTERN_EVENT = 65     # marks which pattern the following events belong to
_NOTE_SIZE = 24
_VELOCITY_OFFSET = 21
_KEY_OFFSET = 12


def _read_varint(buffer: bytes, position: int) -> tuple[int, int]:
    """Read a base-128 varint; return (value, position_after_it)."""
    value = shift = 0
    while True:
        byte = buffer[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, position
        shift += 7


def _patch_velocity_bytes(raw: bytearray, target_pattern_iids: set,
                          velocity_by_key_number: dict, fl_major_version: int) -> int:
    """Set the velocity byte of matching notes in the target patterns, in place.

    A note is touched only if it lives in a target pattern and plays a key that
    is in the map, and only its single velocity byte changes. Returns how many
    notes were changed.
    """
    position = 22                    # skip the file header; events start here
    current_pattern_iid = None
    changed = 0
    while position < len(raw):
        event_id = raw[position]
        position += 1
        if event_id < _EVENT_WORD:
            data_at, data_len = position, 1
            position += 1
        elif event_id < _EVENT_DWORD:
            data_at, data_len = position, 2
            position += 2
        elif event_id < _EVENT_TEXT:
            if event_id == 0xAC and fl_major_version >= 26:
                data_len, position = _read_varint(raw, position)
                data_at = position
                position += data_len
            else:
                data_at, data_len = position, 4
                position += 4
        else:
            data_len, position = _read_varint(raw, position)
            data_at = position
            position += data_len

        if event_id == _NEW_PATTERN_EVENT:
            current_pattern_iid = int.from_bytes(raw[data_at:data_at + 2], "little")
        elif event_id == _NOTES_EVENT and current_pattern_iid in target_pattern_iids:
            for note_at in range(data_at, data_at + data_len, _NOTE_SIZE):
                key_number = int.from_bytes(
                    raw[note_at + _KEY_OFFSET:note_at + _KEY_OFFSET + 2], "little")
                wanted = velocity_by_key_number.get(key_number)
                if wanted is not None and raw[note_at + _VELOCITY_OFFSET] != wanted:
                    raw[note_at + _VELOCITY_OFFSET] = wanted
                    changed += 1
    return changed


class FlStudioProject:
    """A loaded .flp file and the few things we need to do with it."""

    def __init__(self, project, file_path: Path) -> None:
        self._project = project
        self._file_path = file_path

    @classmethod
    def open(cls, file_path: Path) -> "FlStudioProject":
        project = pyflp.parse(str(file_path))
        return cls(project, file_path)

    # --- looking around the project ------------------------------------

    def playlist_track_names_with_clips(self) -> list[str]:
        """Names of playlist tracks that have at least one drum clip on them.

        In playlist order, de-duplicated. These are exactly the tracks you can
        pick as "the drum track".
        """
        names = []
        already_seen = set()
        for arrangement in self._project.arrangements:
            for track in arrangement.tracks:
                if not track.name or track.name in already_seen:
                    continue
                if any(isinstance(clip, PatternPLItem) for clip in track):
                    already_seen.add(track.name)
                    names.append(track.name)
        return names

    def patterns_on_playlist_track(self, track_name: str) -> list:
        """Every distinct pattern that has a clip on the named track."""
        found = []
        already_seen = set()
        for arrangement in self._project.arrangements:
            for track in arrangement.tracks:
                if not _same_name(track.name, track_name):
                    continue
                for clip in track:
                    if isinstance(clip, PatternPLItem) and clip.pattern.iid not in already_seen:
                        already_seen.add(clip.pattern.iid)
                        found.append(clip.pattern)
        return found

    # --- reading and copying velocities --------------------------------

    def velocity_of_each_drum_in(self, pattern) -> dict[str, int]:
        """Map of drum -> velocity for one pattern (drum = piano-roll key)."""
        velocity_of_drum = {}
        for note in pattern.notes:
            velocity_of_drum[note.key] = note.velocity
        return velocity_of_drum

    def drums_used_in(self, patterns: list) -> set[str]:
        """All drums (keys) that appear across the given patterns."""
        drums = set()
        for pattern in patterns:
            for note in pattern.notes:
                drums.add(note.key)
        return drums

    # --- applying the change (surgical: only velocity bytes, never a re-save) ---

    def apply_and_save(self, corrected_pattern, target_patterns) -> tuple:
        """Copy the corrected pattern's velocities onto the target patterns by
        editing only velocity bytes directly in the file. Backs up the original
        first. Returns (notes_changed, backup_path).
        """
        target_iids = {pattern.iid for pattern in target_patterns}
        velocity_by_key_number = {
            note["key"]: note.velocity for note in corrected_pattern.notes
        }
        version_head = str(self._project.version or "").split(".", 1)[0]
        fl_major_version = int(version_head) if version_head.isdigit() else 0

        raw = bytearray(self._file_path.read_bytes())
        changed = _patch_velocity_bytes(
            raw, target_iids, velocity_by_key_number, fl_major_version)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = self._file_path.with_suffix(f".backup_{timestamp}.flp")
        shutil.copy2(self._file_path, backup_path)
        self._file_path.write_bytes(raw)
        return changed, backup_path


# ======================================================================
# 3. The conversation
#    The step-by-step questions, the report, and the apply. This is what
#    runs when you start the program.
# ======================================================================

def ask(question: str) -> str:
    """Ask something; an empty answer means the user wants to quit."""
    answer = input(question).strip().strip('"').strip("'")
    if not answer:
        print("\nNothing entered - leaving without changing anything.")
        sys.exit(0)
    return answer


def ask_for_project() -> FlStudioProject:
    print("Step 1 of 3  -  Which FL Studio project?")
    while True:
        file_path = Path(ask("  Drag the .flp file here (or type its path): "))
        if not file_path.is_file():
            print(f"  I can't find that file. Try again.\n")
            continue
        try:
            project = FlStudioProject.open(file_path)
        except Exception as error:
            print(f"  I couldn't open that project: {error}\n")
            continue
        print(f"  Opened: {file_path.name}\n")
        return project


def ask_for_track(project: FlStudioProject) -> str:
    print("Step 2 of 3  -  Which playlist track holds all the drum clips?")
    track_names = project.playlist_track_names_with_clips()
    if not track_names:
        print("  This project has no playlist tracks with clips on them. Leaving.")
        sys.exit(0)
    for number, name in enumerate(track_names, start=1):
        print(f"  {number}. {name}")
    while True:
        choice = ask("  Enter the number of the drum track: ")
        if choice.isdigit() and 1 <= int(choice) <= len(track_names):
            print()
            return track_names[int(choice) - 1]
        print(f"  Please enter a number between 1 and {len(track_names)}.\n")


def ask_for_corrected_pattern(project: FlStudioProject, track_name: str):
    print("Step 3 of 3  -  Which clip did you already correct?")
    clips = project.patterns_on_playlist_track(track_name)
    for number, clip in enumerate(clips, start=1):
        print(f"  {number}. {clip.name or f'(unnamed {clip.iid})'}")
    while True:
        choice = ask("  Enter the number of your corrected clip: ")
        if choice.isdigit() and 1 <= int(choice) <= len(clips):
            print()
            return clips[int(choice) - 1]
        print(f"  Please enter a number between 1 and {len(clips)}.\n")


def show_findings(velocity_of_drum, target_clips, clips_by_untouched_drum) -> None:
    print("Here is what I found.\n")

    print("Velocities in the clip you corrected:")
    for drum in sorted(velocity_of_drum, key=keyboard_position_of_drum):
        print(f"  {name_of_drum(drum):<20} {velocity_of_drum[drum]}")

    print(f"\nI will copy these into {len(target_clips)} other drum clips:")
    print("  " + ", ".join(c.name or f"(unnamed {c.iid})" for c in target_clips))

    if clips_by_untouched_drum:
        print("\nLeft untouched - these drums are not in the clip you corrected.")
        print("For each one, the clips where it stays exactly as it is now:")
        for drum in sorted(clips_by_untouched_drum, key=keyboard_position_of_drum):
            clips = ", ".join(clips_by_untouched_drum[drum])
            print(f"  {name_of_drum(drum)}: {clips}")
    else:
        print("\nEvery drum in every clip is present in your corrected clip - "
              "nothing left untouched.")


def main() -> None:
    print("=== Drum velocity copier ===")
    print("I copy the velocities from one corrected pattern onto all the other")
    print("drum clips on the same playlist track. Leave any answer blank to quit.\n")

    project = ask_for_project()
    track_name = ask_for_track(project)
    corrected_pattern = ask_for_corrected_pattern(project, track_name)

    velocity_of_drum = project.velocity_of_each_drum_in(corrected_pattern)
    if not velocity_of_drum:
        print("That pattern has no notes, so there is nothing to copy. Leaving.")
        return

    target_clips = [
        p for p in project.patterns_on_playlist_track(track_name)
        if p.iid != corrected_pattern.iid
    ]
    if not target_clips:
        print("There are no other drum clips on that track to change. Leaving.")
        return

    # For every drum missing from the corrected clip, list the clips that use it.
    source_drums = set(velocity_of_drum)
    clips_by_untouched_drum = {}
    for clip in target_clips:
        clip_name = clip.name or f"(unnamed {clip.iid})"
        for drum in project.drums_used_in([clip]):
            if drum not in source_drums:
                clips_by_untouched_drum.setdefault(drum, []).append(clip_name)

    show_findings(velocity_of_drum, target_clips, clips_by_untouched_drum)

    print()
    if ask("Apply these velocities and save? A backup is made first (yes/no): ").lower() \
            not in {"yes", "y"}:
        print("Left unchanged.")
        return

    changed, backup_path = project.apply_and_save(corrected_pattern, target_clips)
    print(f"\nDone. Changed {changed} notes.")
    print(f"Backup of the original: {backup_path.name}")


if __name__ == "__main__":
    main()