import sys

import pytest

from argus import plugin_loader


@pytest.fixture
def fake_plugins_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", tmp_path)
    # argus.plugins is a real package on sys.path -- importlib resolves
    # "argus.plugins.<name>" against argus/plugins/'s actual __path__,
    # not PLUGINS_DIR, so point the package's search path at tmp_path too.
    import argus.plugins as plugins_pkg
    monkeypatch.setattr(plugins_pkg, "__path__", [str(tmp_path)])
    yield tmp_path
    for name in list(sys.modules):
        if name.startswith("argus.plugins.") and name not in ("argus.plugins.example_dice",):
            del sys.modules[name]


def test_no_plugins_dir_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", tmp_path / "does_not_exist")
    assert plugin_loader.load_plugin_tools() == []


def test_discovers_a_module_level_tool(fake_plugins_dir):
    (fake_plugins_dir / "my_plugin.py").write_text(
        "from argus.tools.base import PermissionTier, Tool\n"
        "my_tool = Tool(name='my_test_tool', description='d', input_schema={'type':'object','properties':{}}, "
        "tier=PermissionTier.ALLOW, handler=lambda args: 'ok')\n"
    )
    tools = plugin_loader.load_plugin_tools()
    assert any(t.name == "my_test_tool" for t in tools)


def test_underscore_prefixed_modules_are_skipped(fake_plugins_dir):
    (fake_plugins_dir / "_private.py").write_text(
        "from argus.tools.base import PermissionTier, Tool\n"
        "t = Tool(name='should_not_load', description='d', input_schema={'type':'object','properties':{}}, "
        "tier=PermissionTier.ALLOW, handler=lambda args: 'ok')\n"
    )
    tools = plugin_loader.load_plugin_tools()
    assert not any(t.name == "should_not_load" for t in tools)


def test_broken_plugin_is_skipped_not_raised(fake_plugins_dir):
    (fake_plugins_dir / "broken.py").write_text("this is not valid python (((\n")
    (fake_plugins_dir / "good.py").write_text(
        "from argus.tools.base import PermissionTier, Tool\n"
        "t = Tool(name='good_tool', description='d', input_schema={'type':'object','properties':{}}, "
        "tier=PermissionTier.ALLOW, handler=lambda args: 'ok')\n"
    )
    tools = plugin_loader.load_plugin_tools()  # must not raise
    assert any(t.name == "good_tool" for t in tools)


def test_non_tool_module_level_values_are_ignored(fake_plugins_dir):
    (fake_plugins_dir / "misc.py").write_text("SOME_CONSTANT = 42\ndef helper(): pass\n")
    tools = plugin_loader.load_plugin_tools()
    assert tools == []


def test_real_example_dice_plugin_is_discovered():
    """The shipped example plugin should load via the real (non-monkeypatched) loader."""
    tools = plugin_loader.load_plugin_tools()
    assert any(t.name == "roll_dice" for t in tools)
