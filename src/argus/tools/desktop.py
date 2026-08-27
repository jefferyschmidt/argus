import io
import subprocess

from argus.tools.base import PermissionTier, Tool


def _take_screenshot(args: dict) -> bytes:
    import pyautogui

    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _list_windows(args: dict) -> str:
    import pygetwindow as gw

    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    return "\n".join(titles) if titles else "(no visible windows)"


def _click(args: dict) -> str:
    import pyautogui

    x, y = args["x"], args["y"]
    if args.get("double"):
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y)
    return f"clicked at ({x}, {y})"


def _type_text(args: dict) -> str:
    import pyautogui

    pyautogui.write(args["text"], interval=0.02)
    return f"typed {len(args['text'])} characters"


def _press_key(args: dict) -> str:
    import pyautogui

    keys = args["keys"] if isinstance(args["keys"], list) else [args["keys"]]
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)
    return f"pressed {'+'.join(keys)}"


def _open_app(args: dict) -> str:
    result = subprocess.run(
        f'start "" "{args["app"]}"', shell=True, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return f"error: failed to open '{args['app']}': {result.stderr}"
    return f"opened {args['app']}"


take_screenshot_tool = Tool(
    name="take_screenshot",
    description="Take a screenshot of the entire desktop. Returns the image so you can see what's on screen.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_take_screenshot,
)

list_windows_tool = Tool(
    name="list_windows",
    description="List the titles of all open windows.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_windows,
)

click_tool = Tool(
    name="click",
    description=(
        "Click at pixel coordinates (x, y) on screen. Take a screenshot first to see where "
        "to click -- coordinates are absolute screen pixels, origin top-left."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "double": {"type": "boolean", "description": "Double-click instead of single-click"},
        },
        "required": ["x", "y"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_click,
)

type_text_tool = Tool(
    name="type_text",
    description="Type text at the current cursor/focus location, as if typed on the keyboard.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_type_text,
)

press_key_tool = Tool(
    name="press_key",
    description=(
        "Press a key or key combination. For a single key pass e.g. 'enter', 'tab', 'esc'. "
        "For a combination pass a list e.g. ['ctrl', 'c'] for copy, ['alt', 'tab'] to switch windows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "keys": {
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]
            }
        },
        "required": ["keys"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_press_key,
)

open_app_tool = Tool(
    name="open_app",
    description="Open an application by name (e.g. 'notepad', 'calc') or a file/folder path.",
    input_schema={
        "type": "object",
        "properties": {"app": {"type": "string"}},
        "required": ["app"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_open_app,
)
