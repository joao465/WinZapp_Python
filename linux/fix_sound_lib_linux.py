#!/usr/bin/env python3
"""Patch sound-lib's Linux default-output bug for WinZapp builds.

sound-lib 0.83 rewrites BASS device -1 (system default) to device 1 on Linux.
That can bypass the PipeWire/ALSA default PCM and make BASS_Init fail with
BASS_ERROR_DRIVER (3) even while the desktop audio stack is working.

This build-time patch keeps -1 unchanged so BASS itself opens the configured
system default output. Windows and macOS behavior are untouched.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import sound_lib.output


def main() -> int:
    path = Path(inspect.getsourcefile(sound_lib.output) or sound_lib.output.__file__).resolve()
    text = path.read_text(encoding="utf-8")

    pattern = re.compile(
        r"(?P<indent>^[ \t]*)if\s*(?:\(\s*)?"
        r"platform\.system\(\)\s*==\s*['\"]Linux['\"]\s*"
        r"and\s*device\s*==\s*-1\s*(?:\)\s*)?:[^\n]*\n"
        r"(?P=indent)[ \t]+device\s*=\s*1\s*\n",
        re.MULTILINE,
    )

    replacement = (
        r"\g<indent># WinZapp Linux: keep device=-1 so BASS uses the real "
        r"system default (PipeWire/ALSA).\n"
    )
    patched, count = pattern.subn(replacement, text, count=1)

    if count != 1:
        if "WinZapp Linux: keep device=-1" in text:
            print(f"sound-lib Linux default-device patch already applied: {path}")
            return 0
        raise SystemExit(
            f"Could not find sound-lib Linux device=-1 -> device=1 block in {path}"
        )

    compile(patched, str(path), "exec")
    path.write_text(patched, encoding="utf-8")

    verify = path.read_text(encoding="utf-8")
    if re.search(r"platform\.system\(\).*Linux", verify) and re.search(
        r"device\s*=\s*1", verify
    ):
        # Do not fail on unrelated device assignments elsewhere; the exact buggy
        # block was already removed above. This message is diagnostic only.
        print("Note: other Linux/device references remain in sound-lib output.py")

    print(f"Patched sound-lib Linux default output: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
