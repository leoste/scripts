#!/usr/bin/env python3
r"""
fix_russian_tags.py

Repairs Russian MP3 tags that show up as gibberish, for example
"Ïîäáåðó ìóçûêó" instead of "Подберу музыку".

Why it happens
    Old Russian tagging software stored the text as Windows-1251 bytes but
    labelled it Latin-1 (or put it in an ID3v1 tag, which has no encoding
    label at all). Modern players trust the label and show gibberish. Some
    programs later copied that gibberish into an APEv2 tag, so it is wrong
    there too.

What the script does, for every .mp3 file in the folders you give it
    * ID3v2 tag: every text field that looks like misread Russian is
      converted back and saved as UTF-16. The ID3v2 version (2.3 / 2.4) is kept.
    * APEv2 tag: the same check on its text fields. ReplayGain / MP3Gain
      values are plain numbers and are left alone.
    * File with only an ID3v1 tag: an ID3v2 tag is created from the repaired
      values.
    * The ID3v1 tag is kept byte for byte (players ignore it when an ID3v2
      tag exists). Add --remove-id3v1 to delete it.

Safety
    * Without --apply nothing is written; it only prints what would change.
    * Text that isn't clearly Russian is left alone, so Estonian ("Õnne",
      "Päike"), Spanish ("Bésame") etc. are not touched.
    * Values with fewer than 3 Cyrillic letters after conversion (like a
      one-letter title "Я") are only reported, marked with "?", because they
      are too short to tell apart from accented Latin text.

Needs Python 3.8+ and mutagen:   pip install mutagen

Examples
    python fix_russian_tags.py "U:\meedia\unsorted\Яак Йоала (Jaak Joala)"
    python fix_russian_tags.py "U:\meedia\unsorted\Яак Йоала (Jaak Joala)" --log report.txt
    python fix_russian_tags.py "U:\meedia\unsorted\Яак Йоала (Jaak Joala)" --apply
"""

import argparse
import codecs
import os
import re
import sys

try:
    from mutagen.apev2 import APEv2, APENoHeaderError, APETextValue
    from mutagen.id3 import ID3, ID3NoHeaderError, Encoding
except ImportError:
    sys.exit("This script needs the mutagen library. Install it with:  pip install mutagen")


EXTENSIONS = {".mp3"}

# A value is only changed if it has at least this many Cyrillic letters after repair.
MIN_CYRILLIC_LETTERS = 3

CYRILLIC_LETTER = re.compile("[А-Яа-яЁёІіЇїЄєҐґЎў]")
LATIN_LETTER = re.compile("[A-Za-z]")
WORD = re.compile(r"\w+")
# Non-letter characters that normally appear in Windows-1251 text.
ALLOWED_SYMBOLS = set("«»–—…№“”„‘’‚•·©®°±µ§€™\u00a0")

ID3_FIELD_NAMES = {
    "TIT2": "title", "TPE1": "artist", "TPE2": "album artist", "TALB": "album",
    "TCOM": "composer", "TEXT": "lyricist", "TCON": "genre", "TIT1": "grouping",
    "TIT3": "subtitle", "TPE3": "conductor", "TPE4": "remixer", "TOPE": "orig. artist",
    "TPUB": "publisher", "TCOP": "copyright", "TENC": "encoded by",
    "COMM": "comment", "USLT": "lyrics", "TXXX": "custom", "APIC": "picture",
}


# ---------------------------------------------------------------- text repair

def to_legacy_bytes(text):
    """Turn misread text back into the bytes it was read from.

    Works whether those bytes were read as Latin-1 or as Windows-1252.
    Returns None if the text has characters neither could have produced
    (for example real Cyrillic), which means it isn't this kind of gibberish.
    """
    out = bytearray()
    for ch in text:
        if ord(ch) < 256:
            out.append(ord(ch))
        else:
            try:
                out += ch.encode("cp1252")
            except UnicodeEncodeError:
                return None
    return bytes(out)


def repair(text, codepage):
    """Return (repaired_text, confident) if text looks like misread Cyrillic, else None."""
    if not text or text.isascii():
        return None
    raw = to_legacy_bytes(text)
    if raw is None:
        return None
    try:
        fixed = raw.decode(codepage)
    except UnicodeDecodeError:
        return None
    if fixed == text:
        return None

    # Every non-ASCII character must now be a Cyrillic letter or normal punctuation.
    for ch in fixed:
        if ord(ch) > 127 and ch not in ALLOWED_SYMBOLS and not CYRILLIC_LETTER.match(ch):
            return None

    # Accented Latin text ("Õnne", "Bésame", "Müller") turns into words that are
    # mostly Latin letters with a stray Cyrillic one ("Хnne"). Misread Russian
    # turns into words that are entirely, or almost entirely, Cyrillic.
    # Words without any Cyrillic ("Instrumental", "Radar") don't count either way.
    cyrillic = latin = 0
    for word in WORD.findall(fixed):
        c = len(CYRILLIC_LETTER.findall(word))
        if c:
            cyrillic += c
            latin += len(LATIN_LETTER.findall(word))
    if cyrillic <= 2 * latin:
        return None
    return fixed, cyrillic >= MIN_CYRILLIC_LETTERS


class FileReport:
    """What was (or would be) changed in one file."""

    def __init__(self):
        self.changes = []  # (field, old, new)
        self.unsure = []   # (field, old, would_be)
        self.notes = []

    def check(self, value, field, codepage):
        """Return the repaired value if it should be changed, otherwise None."""
        result = repair(value, codepage)
        if result is None:
            return None
        fixed, confident = result
        if confident:
            self.changes.append((field, value, fixed))
            return fixed
        self.unsure.append((field, value, fixed))
        return None


# ---------------------------------------------------------------- tag handling

def fix_id3(tags, codepage, report):
    """Repair text in ID3v2 frames in memory. Returns True if anything changed."""
    changed = False
    for key, frame in list(tags.items()):
        field = f"ID3v2 {frame.FrameID}"
        if frame.FrameID in ID3_FIELD_NAMES:
            field += f" ({ID3_FIELD_NAMES[frame.FrameID]})"
        text_changed = False

        text = getattr(frame, "text", None)
        if isinstance(text, str):  # USLT (lyrics) holds a single string
            new = report.check(text, field, codepage)
            if new is not None:
                frame.text = new
                text_changed = True
        elif isinstance(text, list):  # normal text frames, COMM, TXXX
            new_list = []
            for item in text:
                new = report.check(item, field, codepage) if isinstance(item, str) else None
                if new is not None:
                    text_changed = True
                new_list.append(item if new is None else new)
            if text_changed:
                frame.text = new_list

        desc = getattr(frame, "desc", None)
        new_desc = report.check(desc, field + " description", codepage) if isinstance(desc, str) else None

        if not text_changed and new_desc is None:
            continue
        if hasattr(frame, "encoding"):
            frame.encoding = Encoding.UTF16  # valid in both ID3v2.3 and 2.4
        if new_desc is not None:
            # The description is part of the frame's key, so re-insert it under the new key.
            del tags[key]
            frame.desc = new_desc
            tags.add(frame)
        changed = True
    return changed


def fix_ape(ape, codepage, report):
    """Repair text in APEv2 items in memory. Returns True if anything changed."""
    changed = False
    for key in list(ape.keys()):
        value = ape[key]
        if not isinstance(value, APETextValue):
            continue
        parts = str(value).split("\0")  # APE keeps multiple values separated by NUL
        new_parts = []
        item_changed = False
        for part in parts:
            new = report.check(part, f"APEv2 {key}", codepage)
            if new is not None:
                item_changed = True
            new_parts.append(part if new is None else new)
        if item_changed:
            ape[key] = new_parts
            changed = True
    return changed


def id3v2_major_version(path):
    """Return 2, 3 or 4 for a file starting with an ID3v2 tag, else None."""
    with open(path, "rb") as f:
        head = f.read(10)
    return head[3] if len(head) == 10 and head[:3] == b"ID3" else None


def read_id3v1(path):
    """Return the 128-byte ID3v1 tag at the end of the file, or None."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() < 128:
            return None
        f.seek(-128, os.SEEK_END)
        data = f.read(128)
    return data if data[:3] == b"TAG" else None


def write_id3v1(path, data):
    """Make the file end with `data` as its ID3v1 tag, or with no ID3v1 tag if data is None."""
    with open(path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        has_tag = False
        if size >= 128:
            f.seek(size - 128)
            has_tag = f.read(3) == b"TAG"
        if data is None and has_tag:
            f.truncate(size - 128)
        elif data is not None and not has_tag:
            f.seek(0, os.SEEK_END)
            f.write(data)


def process_file(path, args):
    """Check one file and, with --apply, save the repairs.

    Returns (report, needs_write, has_legacy_id3v1).
    """
    report = FileReport()
    id3v1 = read_id3v1(path)

    # ID3v2 tag, or the ID3v1 values turned into ID3v2 frames if that's all there is.
    # load_v1=False keeps ID3v1 values from being mixed into an existing ID3v2 tag.
    major = id3v2_major_version(path)
    save_version = 4 if major == 4 else 3
    tags = None
    if major is not None:
        tags = ID3(path, v2_version=save_version, load_v1=False)
    elif id3v1 is not None:
        try:
            tags = ID3(path, v2_version=3, load_v1=True)
        except ID3NoHeaderError:
            tags = None
    id3_changed = tags is not None and fix_id3(tags, args.codepage, report)
    if id3_changed and major is None:
        report.notes.append("no ID3v2 tag yet: create one from the repaired ID3v1 values")

    # APEv2 tag
    try:
        ape = APEv2(path)
    except APENoHeaderError:
        ape = None
    ape_changed = ape is not None and fix_ape(ape, args.codepage, report)

    remove_v1 = args.remove_id3v1 and id3v1 is not None
    if remove_v1:
        report.notes.append("remove the ID3v1 tag")

    needs_write = id3_changed or ape_changed or remove_v1
    if args.apply and needs_write:
        try:
            if ape_changed:
                ape.save(path)
            if id3_changed:
                tags.save(path, v1=0, v2_version=save_version)
        finally:
            # mutagen drops the ID3v1 tag while saving; put the original bytes
            # back unless --remove-id3v1 was given.
            write_id3v1(path, None if args.remove_id3v1 else id3v1)

    has_legacy_v1 = (id3v1 is not None and not remove_v1
                     and any(b > 127 for b in id3v1[3:93]))
    return report, needs_write, has_legacy_v1


# ---------------------------------------------------------------- command line

class Output:
    """Prints to the console and, optionally, to a UTF-8 log file."""

    def __init__(self, log_path):
        self.log = open(log_path, "w", encoding="utf-8") if log_path else None

    def __call__(self, line=""):
        print(line)
        if self.log:
            self.log.write(line + "\n")

    def close(self):
        if self.log:
            self.log.close()


def clip(text, limit=70):
    text = text.replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def find_mp3s(paths, out):
    """Yield (full_path, path_to_show) for every MP3 in the given folders/files."""
    for root in paths:
        if os.path.isdir(root):
            out(f"== {os.path.abspath(root)}")
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames.sort()
                for name in sorted(filenames):
                    if os.path.splitext(name)[1].lower() in EXTENSIONS:
                        full = os.path.join(dirpath, name)
                        yield full, os.path.relpath(full, root)
        elif os.path.isfile(root):
            yield root, root
        else:
            out(f"Not found: {root}")


def main():
    parser = argparse.ArgumentParser(
        description="Repair Russian MP3 tags that show up as gibberish "
                    "(Windows-1251 text read as Latin-1). Preview only unless --apply is given.")
    parser.add_argument("paths", nargs="+", help="folders (searched with subfolders) and/or .mp3 files")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: only show them)")
    parser.add_argument("--remove-id3v1", action="store_true", help="also delete the old ID3v1 tag")
    parser.add_argument("--codepage", default="cp1251",
                        help="code page the text was really written in (default: cp1251; "
                             "others: koi8_r, cp866)")
    parser.add_argument("--log", help="also write the report to this file (UTF-8)")
    args = parser.parse_args()
    try:
        codecs.lookup(args.codepage)
    except LookupError:
        parser.error(f"unknown code page: {args.codepage}")

    # Don't crash on Cyrillic when the output is redirected on Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    out = Output(args.log)
    try:
        out("APPLYING CHANGES" if args.apply else
            "PREVIEW - nothing is written. Add --apply to save the changes.")
        scanned = files_changed = values_changed = unsure = legacy_v1 = errors = 0

        for path, shown in find_mp3s(args.paths, out):
            scanned += 1
            try:
                report, needs_write, has_legacy_v1 = process_file(path, args)
            except Exception as e:  # keep going with the other files
                errors += 1
                out(shown)
                out(f"    ERROR: {type(e).__name__}: {e}")
                continue

            legacy_v1 += has_legacy_v1
            unsure += len(report.unsure)
            if needs_write:
                files_changed += 1
                values_changed += len(report.changes)
            if report.changes or report.unsure or report.notes:
                out(shown)
                for field, old, new in report.changes:
                    out(f"    {field:<26} {clip(old)!r}  ->  {clip(new)!r}")
                for field, old, new in report.unsure:
                    out(f"  ? {field:<26} {clip(old)!r}  ->  {clip(new)!r}  (too short to be sure, NOT changed)")
                for note in report.notes:
                    out(f"    {note}")

        out()
        out(f"Scanned {scanned} MP3 file(s).")
        out(f"{'Changed' if args.apply else 'Would change'} {files_changed} file(s), "
            f"{values_changed} text value(s).")
        if unsure:
            out(f"{unsure} short value(s) marked with ? were left alone. Check those by hand.")
        if legacy_v1:
            out(f"{legacy_v1} file(s) keep an old ID3v1 tag with non-English bytes (probably "
                f"Windows-1251). Players ignore it when ID3v2 exists; --remove-id3v1 deletes it.")
        if errors:
            out(f"{errors} file(s) could not be processed (see the ERROR lines).")
        if not args.apply and files_changed:
            out("Nothing was written. Run again with --apply to save the changes.")
    finally:
        out.close()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
