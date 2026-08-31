#!/usr/bin/env python3
"""Harden sound-lib's Linux output initialization for WinZapp builds.

The pinned sound-lib 0.83 package has two problems for modern Linux desktops:
1. it rewrites BASS device -1 (system default) to device 1;
2. a BASS_Init failure aborts the whole application.

This build-time patch keeps -1 intact, then makes Linux initialization robust:
- try the requested/default device first;
- if that fails, enumerate enabled BASS output devices and try them one by one;
- if no real output can be opened, initialize BASS device 0 ("no sound") so
  the WinZapp UI can still start and diagnostics can continue.

Windows and macOS behavior are untouched.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import sound_lib.output


MARKER = "WINZAPP_LINUX_BASS_FALLBACK"


def main() -> int:
    path = Path(inspect.getsourcefile(sound_lib.output) or sound_lib.output.__file__).resolve()
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print(f"sound-lib Linux BASS fallback already applied: {path}")
        return 0

    # Remove sound-lib 0.83's legacy rewrite from default (-1) to device 1.
    pattern = re.compile(
        r"(?P<indent>^[ \t]*)if\s*(?:\(\s*)?"
        r"platform\.system\(\)\s*==\s*['\"]Linux['\"]\s*"
        r"and\s*device\s*==\s*-1\s*(?:\)\s*)?:[^\n]*\n"
        r"(?P=indent)[ \t]+device\s*=\s*1\s*\n",
        re.MULTILINE,
    )
    text, removed = pattern.subn(
        r"\g<indent># WinZapp Linux: keep device=-1 as the real system default.\n",
        text,
        count=1,
    )
    if removed != 1:
        raise SystemExit(f"Could not find legacy Linux device=-1 -> device=1 block in {path}")

    old_call = "        bass_call(BASS_Init, device, frequency, flags, window, clsid)\n"
    new_call = '''        # WINZAPP_LINUX_BASS_FALLBACK
        if platform.system() == "Linux":
            # BASS documents -1 as the system default and 0 as the no-sound
            # device. Modern PipeWire/ALSA systems may expose several real
            # devices, so do not let one failing default abort the application.
            candidates = [device]
            if device == -1:
                try:
                    _info = BASS_DEVICEINFO()
                    _idx = 1
                    while BASS_GetDeviceInfo(_idx, pointer(_info)):
                        try:
                            _enabled = bool(_info.flags & BASS_DEVICE_ENABLED)
                        except Exception:
                            _enabled = True
                        if _enabled and _idx not in candidates:
                            candidates.append(_idx)
                        _idx += 1
                        _info = BASS_DEVICEINFO()
                except Exception:
                    # Enumeration is diagnostic/fallback only; the default
                    # attempt below remains valid even if enumeration fails.
                    pass

            _last_error = None
            for _candidate in candidates:
                try:
                    bass_call(BASS_Init, _candidate, frequency, flags, window, clsid)
                    self._device = _candidate
                    return
                except Exception as _exc:
                    _last_error = _exc
                    try:
                        BASS_Free()
                    except Exception:
                        pass

            # Last-resort startup mode: BASS device 0 is the documented
            # "no sound" device. This deliberately keeps the UI alive so the
            # user can use WinZapp and provide diagnostics even when BASS has
            # no usable Linux playback driver.
            try:
                bass_call(BASS_Init, 0, frequency, flags, window, clsid)
                self._device = 0
                return
            except Exception:
                if _last_error is not None:
                    raise _last_error
                raise
        else:
            bass_call(BASS_Init, device, frequency, flags, window, clsid)
'''

    if old_call not in text:
        raise SystemExit(f"Could not find BASS_Init call in {path}")
    text = text.replace(old_call, new_call, 1)

    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")
    print(f"Patched sound-lib Linux BASS fallback: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
