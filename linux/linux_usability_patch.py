#!/usr/bin/env python3
"""Linux-only build patch for Orca, automatic locale and short AF_UNIX IPC.

The upstream WinZapp UI contains Windows-specific accessibility helpers and a
first-run language chooser. The Linux portable build should instead use
native GTK/AT-SPI semantics, follow the desktop locale automatically and keep
its Unix-domain socket below Linux's sun_path length limit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


MARKER = "WINZAPP_LINUX_USABILITY_PATCH"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"Could not find {label}")
    return text.replace(old, new, 1)


def patch_main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return

    pattern = re.compile(
        r"(?ms)^    def _ensure_language_selected\(self\):\n.*?(?=^    def [A-Za-z_])"
    )
    replacement = '''    def _ensure_language_selected(self):
        """Linux portable: select the desktop language without a startup dialog."""
        # WINZAPP_LINUX_USABILITY_PATCH
        lang_already_set = self.settings.get("general", {}).get("language", "")
        if lang_already_set:
            return
        from core.i18n import detect_system_language
        lang = detect_system_language()
        self.settings.setdefault("general", {})["language"] = lang
        self.save_settings()
        logging.info("[linux] detected system language: %s", lang)

'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Could not patch _ensure_language_selected in {path}")

    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def patch_conversations(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    if "import sys\n" not in text:
        text = replace_once(text, "import re\n", "import re\nimport sys\n", "conversations sys import")

    text = replace_once(
        text,
        '        self.search_field = wx.TextCtrl(self, style=wx.TE_DONTWRAP)\n',
        '        self.search_field = wx.TextCtrl(self, style=wx.TE_DONTWRAP)\n'
        '        self.search_field.SetName(i18n.t("search_conversations"))\n',
        "conversation search accessible name",
    )

    text = replace_once(
        text,
        '        self.conversations_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)\n',
        '        self.conversations_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)\n'
        '        self.conversations_list.SetName(i18n.t("conversations"))\n',
        "conversations list accessible name",
    )

    text = replace_once(
        text,
        '        self._search_field = wx.TextCtrl(self._search_panel, style=wx.TE_DONTWRAP | wx.TE_PROCESS_ENTER)\n',
        '        self._search_field = wx.TextCtrl(self._search_panel, style=wx.TE_DONTWRAP | wx.TE_PROCESS_ENTER)\n'
        '        self._search_field.SetName(i18n.t("search_in_conv"))\n',
        "in-conversation search accessible name",
    )

    # Do not depend on the exact formatting of the preceding dataview fallback.
    # The Linux accessibility patch may alter nearby lines before this patch runs.
    # The assignment itself is stable and is the only point that needs overriding.
    mode_assignment = '        self._message_list_mode = message_list_mode\n'
    linux_mode_assignment = (
        '        # Linux/Orca: prefer the simpler GTK-backed compatibility list.\n'
        '        # It exposes rows more predictably through AT-SPI than wx.ListCtrl.\n'
        '        if sys.platform != "win32":\n'
        '            message_list_mode = "listbox"\n'
        '        self._message_list_mode = message_list_mode\n'
    )
    text = replace_once(
        text,
        mode_assignment,
        linux_mode_assignment,
        "message-list mode assignment",
    )

    text = replace_once(
        text,
        '        control.InsertColumn(0, label, width=360)\n',
        '        control.InsertColumn(0, label, width=360)\n'
        '        control.SetName(label)\n',
        "messages list accessible name",
    )

    text = replace_once(
        text,
        '        self.message_field.SetHint(i18n.t("type_message"))\n',
        '        self.message_field.SetHint(i18n.t("type_message"))\n'
        '        self.message_field.SetName(i18n.t("type_message"))\n',
        "message composer accessible name",
    )

    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def patch_ipc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old = '''def _ipc_dir(global_dir: str) -> str:
    d = os.path.join(global_dir, "ipc")
    os.makedirs(d, exist_ok=True)
    return d
'''
    new = '''def _ipc_dir(global_dir: str) -> str:
    # Linux AF_UNIX paths are normally limited to roughly 108 bytes. A
    # portable WinZapp can live deep under ~/Downloads, so keeping the socket
    # beside the application can exceed that limit. Put only the transient
    # socket in the user's short runtime directory; persistent data remains
    # portable beside the executable.
    if os.name != "nt":
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        try:
            uid = os.getuid()
        except AttributeError:
            uid = 0
        d = os.path.join(base, f"winzapp-ipc-{uid}")
    else:
        d = os.path.join(global_dir, "ipc")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return d
'''
    text = replace_once(text, old, new, "short Linux IPC directory")
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: linux_usability_patch.py <WinZapp source root>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    client = root / "client"
    if not client.is_dir():
        print(f"client directory not found: {client}", file=sys.stderr)
        return 2

    patch_main(client / "main.py")
    patch_conversations(client / "ui" / "conversations.py")
    patch_ipc(client / "ipc.py")

    print("Linux usability patch applied:")
    print("  - automatic system language on first launch")
    print("  - Orca-friendly native names and message list")
    print("  - short AF_UNIX IPC runtime path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
