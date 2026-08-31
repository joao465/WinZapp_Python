#!/usr/bin/env python3
"""Linux build-time usability patch for Orca, locale and AF_UNIX IPC.

This patch intentionally uses Python's AST instead of matching source-code
formatting. Another Linux accessibility patch rewrites files with ast.unparse(),
so text/indentation-based replacements are inherently brittle.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _import_exists(tree: ast.Module, module: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(alias.name == module for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            return True
    return False


def _ensure_import(tree: ast.Module, module: str) -> None:
    if _import_exists(tree, module):
        return
    insert_at = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        insert_at = 1
    while (
        insert_at < len(tree.body)
        and isinstance(tree.body[insert_at], ast.ImportFrom)
        and tree.body[insert_at].module == "__future__"
    ):
        insert_at += 1
    tree.body.insert(insert_at, ast.Import(names=[ast.alias(name=module)]))


def _method_body(source: str, method_name: str) -> list[ast.stmt]:
    wrapper = ast.parse(source)
    fn = next(
        node
        for node in wrapper.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    return fn.body


class MainTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.patched = 0

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name != "_ensure_language_selected":
            return self.generic_visit(node)
        node.body = _method_body(
            '''
def _template(self):
    # Linux portable: do not show the startup language chooser here.
    # MainWindow calls this before self.i18n exists. I18n is constructed later
    # and already detects the system locale when there is no valid saved override.
    return
''',
            "_template",
        )
        self.patched += 1
        return node


def _self_attr_name(target: ast.AST) -> str | None:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return target.attr
    return None


def _call_stmt(receiver: str, method: str, arg: ast.expr) -> ast.Expr:
    return ast.Expr(
        value=ast.Call(
            func=ast.Attribute(
                value=ast.Attribute(
                    value=ast.Name(id="self", ctx=ast.Load()),
                    attr=receiver,
                    ctx=ast.Load(),
                ),
                attr=method,
                ctx=ast.Load(),
            ),
            args=[arg],
            keywords=[],
        )
    )


def _i18n_t(key: str) -> ast.expr:
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="i18n", ctx=ast.Load()),
            attr="t",
            ctx=ast.Load(),
        ),
        args=[ast.Constant(key)],
        keywords=[],
    )


class ConversationsTransformer(ast.NodeTransformer):
    ACCESSIBLE_NAMES = {
        "search_field": "search_conversations",
        "conversations_list": "conversations",
        "_search_field": "search_in_conv",
        "message_field": "type_message",
    }

    def __init__(self) -> None:
        self._class_stack: list[str] = []
        self._function_stack: list[str] = []
        self.named_controls: set[str] = set()
        self.message_mode_patched = 0
        self.messages_list_named = 0

    @property
    def in_conversations_panel(self) -> bool:
        return bool(self._class_stack) and self._class_stack[-1] == "ConversationsPanel"

    @property
    def current_function(self) -> str:
        return self._function_stack[-1] if self._function_stack else ""

    def visit_ClassDef(self, node: ast.ClassDef):
        self._class_stack.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._function_stack.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self._function_stack.pop()

    def visit_Assign(self, node: ast.Assign):
        node = self.generic_visit(node)
        if not self.in_conversations_panel:
            return node

        if len(node.targets) != 1:
            return node
        attr = _self_attr_name(node.targets[0])

        if self.current_function == "__init__" and attr in self.ACCESSIBLE_NAMES:
            # Only tag assignments that actually create wx controls. This
            # avoids touching later state assignments with the same attribute.
            if isinstance(node.value, ast.Call):
                extra = _call_stmt(
                    attr,
                    "SetName",
                    _i18n_t(self.ACCESSIBLE_NAMES[attr]),
                )
                self.named_controls.add(attr)
                return [node, ast.copy_location(extra, node)]

        if (
            self.current_function == "__init__"
            and attr == "_message_list_mode"
            and isinstance(node.value, ast.Name)
            and node.value.id == "message_list_mode"
        ):
            linux_override = ast.If(
                test=ast.Compare(
                    left=ast.Attribute(
                        value=ast.Name(id="sys", ctx=ast.Load()),
                        attr="platform",
                        ctx=ast.Load(),
                    ),
                    ops=[ast.NotEq()],
                    comparators=[ast.Constant("win32")],
                ),
                body=[
                    ast.Assign(
                        targets=[ast.Name(id="message_list_mode", ctx=ast.Store())],
                        value=ast.Constant("listbox"),
                    )
                ],
                orelse=[],
            )
            self.message_mode_patched += 1
            return [ast.copy_location(linux_override, node), node]

        return node

    def visit_Expr(self, node: ast.Expr):
        node = self.generic_visit(node)
        if (
            self.in_conversations_panel
            and self.current_function == "_create_messages_list_control"
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "InsertColumn"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "control"
        ):
            set_name = ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="control", ctx=ast.Load()),
                        attr="SetName",
                        ctx=ast.Load(),
                    ),
                    args=[ast.Name(id="label", ctx=ast.Load())],
                    keywords=[],
                )
            )
            self.messages_list_named += 1
            return [node, ast.copy_location(set_name, node)]
        return node


class IpcTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.patched = 0

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name != "_ipc_dir":
            return self.generic_visit(node)
        node.body = _method_body(
            '''
def _template(global_dir):
    # Keep AF_UNIX sockets under Linux's short sun_path limit while leaving
    # persistent WinZapp data in the portable data directory.
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
''',
            "_template",
        )
        self.patched += 1
        return node


def _write_tree(path: Path, tree: ast.Module) -> None:
    ast.fix_missing_locations(tree)
    text = ast.unparse(tree) + "\n"
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def patch_main(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tx = MainTransformer()
    tree = tx.visit(tree)
    _write_tree(path, tree)
    return tx.patched


def patch_conversations(path: Path) -> ConversationsTransformer:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _ensure_import(tree, "sys")
    tx = ConversationsTransformer()
    tree = tx.visit(tree)
    _write_tree(path, tree)
    return tx


def patch_ipc(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tx = IpcTransformer()
    tree = tx.visit(tree)
    _write_tree(path, tree)
    return tx.patched


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: linux_usability_patch.py <WinZapp source root>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    client = root / "client"
    if not client.is_dir():
        print(f"client directory not found: {client}", file=sys.stderr)
        return 2

    main_count = patch_main(client / "main.py")
    conv = patch_conversations(client / "ui" / "conversations.py")
    ipc_count = patch_ipc(client / "ipc.py")

    # i18n itself is committed in the fork; compile it here as a build guard.
    i18n_path = client / "core" / "i18n.py"
    compile(i18n_path.read_text(encoding="utf-8"), str(i18n_path), "exec")

    print("Linux usability AST patch applied:")
    print(f"  - language chooser methods patched: {main_count}")
    print(f"  - named Orca controls: {sorted(conv.named_controls)}")
    print(f"  - Linux message-list overrides: {conv.message_mode_patched}")
    print(f"  - messages-list accessible names: {conv.messages_list_named}")
    print(f"  - short AF_UNIX IPC functions patched: {ipc_count}")

    # These diagnostics are intentionally warnings rather than hard failures.
    # If upstream moves a non-critical UI control, the portable build should
    # still complete and remain testable instead of failing on source layout.
    expected = set(ConversationsTransformer.ACCESSIBLE_NAMES)
    missing = sorted(expected - conv.named_controls)
    if main_count == 0:
        print(
            "WARNING: _ensure_language_selected was not found; i18n still uses "
            "system locale when no override is saved."
        )
    if missing:
        print(f"WARNING: optional Orca names not found: {missing}")
    if conv.message_mode_patched == 0:
        print(
            "WARNING: message-list mode assignment not found; leaving upstream "
            "mode unchanged."
        )
    if conv.messages_list_named == 0:
        print(
            "WARNING: message-list InsertColumn not found; native GTK role "
            "remains available."
        )
    if ipc_count == 0:
        print("WARNING: _ipc_dir not found; leaving upstream IPC path unchanged.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
