"""§8's hard constraint, proved by inspection rather than by behavior:
nothing in argus/compose/ may send, post, or upload anything. Parses
every source file in the package with `ast` and asserts every imported
module name is on an explicit allowlist that contains nothing capable
of opening a network connection -- not "we didn't call requests.get()
in this test," but "the module doesn't import anything that could.\""""

import ast
from pathlib import Path

import argus.compose.compose

_COMPOSE_PACKAGE_DIR = Path(argus.compose.compose.__file__).parent

# Everything compose.py is allowed to import. Stdlib pieces needed for
# rendering/atomic-write/paths, plus the two argus modules it genuinely
# needs (settings for the output directory, spine to emit the
# observation). No networking, email, or subprocess module of any kind.
_ALLOWED_MODULES = {
    "html", "string", "time", "uuid", "dataclasses", "pathlib", "typing",
    "argus.config", "argus.spine.observation", "argus.spine.store",
}

# Explicit blocklist too, not just relying on the allowlist -- names a
# module could plausibly slip in under (e.g. via a deep dotted import)
# that would defeat a naive allowlist check based on top-level package
# name alone.
_NETWORK_CAPABLE_SUBSTRINGS = (
    "smtplib", "imaplib", "poplib", "ftplib", "socket", "requests",
    "urllib", "http.client", "httpx", "aiohttp", "websocket", "webbrowser",
    "subprocess", "paramiko", "boto3",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _compose_source_files() -> list[Path]:
    return sorted(p for p in _COMPOSE_PACKAGE_DIR.rglob("*.py"))


def test_compose_package_has_source_files_to_check():
    # Guards the test itself: if this ever returns [], every assertion
    # below passes vacuously and silently stops checking anything.
    assert _compose_source_files()


def test_no_network_capable_module_is_imported_anywhere_in_compose():
    for path in _compose_source_files():
        for module in _imported_modules(path):
            for bad in _NETWORK_CAPABLE_SUBSTRINGS:
                assert bad not in module, f"{path.name} imports {module!r}, which looks network-capable ({bad!r})"


def test_every_import_in_compose_is_on_the_explicit_allowlist():
    for path in _compose_source_files():
        for module in _imported_modules(path):
            assert module in _ALLOWED_MODULES, (
                f"{path.name} imports {module!r}, which is not on the compose package's "
                f"reviewed allowlist ({sorted(_ALLOWED_MODULES)}) -- add it there only after "
                f"confirming it cannot send, post, or upload anything."
            )


def test_no_send_post_upload_named_calls_appear_as_source_text():
    """Belt-and-braces on top of the import check: even a call routed
    through an already-imported, otherwise-innocuous module (e.g. some
    hypothetical argus.tools function) would show up as a call name
    containing one of these verbs."""
    suspicious = ("send_email", "send_message", "post(", ".upload(", "smtp", "publish_external")
    for path in _compose_source_files():
        text = path.read_text(encoding="utf-8").lower()
        for term in suspicious:
            assert term not in text, f"{path.name} contains suspicious text {term!r}"
