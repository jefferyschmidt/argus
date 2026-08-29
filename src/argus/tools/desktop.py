import base64
import io
import subprocess

from argus.tools.base import PermissionTier, Tool
from argus.ui import events as ui_events

# BGR -- matches the console UI's own --accent (#3E9DFF) so the stylized
# camera view reads as part of the same visual language, not a random filter.
_VISION_EDGE_COLOR_BGR = (255, 157, 62)


def _detect_faces(gray):
    """Best-effort Haar-cascade face detection (bundled with opencv, no
    extra download/model needed) -- returns [] on any failure rather than
    breaking the whole rendering, since this is a HUD flourish, not the
    point of the capture."""
    import cv2

    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if cascade.empty():
            return []
        return list(cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)))
    except Exception:
        return []


def _draw_target_reticle(canvas, x, y, w, h):
    """Corner-bracket targeting reticle, the classic computer-vision-HUD
    annotation for a detected object -- genuinely different from a plain
    image filter, closer to what real detector output actually looks
    like."""
    import cv2

    color = _VISION_EDGE_COLOR_BGR
    bracket = max(8, int(min(w, h) * 0.18))
    for cx, cy, sx, sy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        cv2.line(canvas, (cx, cy), (cx + sx * bracket, cy), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (cx, cy), (cx, cy + sy * bracket), color, 2, cv2.LINE_AA)
    cv2.putText(canvas, "TARGET", (x, max(12, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def _stylize_vision(frame):
    """Renders a computer-vision-style rendering instead of the literal
    photo -- the DEFAULT display for capture_camera. Deliberately NOT what
    gets sent to the model (see _capture_camera -- it still analyzes the
    real frame, so it can actually answer questions about it accurately);
    this is purely a display choice, confirmed as an explicit preference:
    show the raw capture only when specifically asked for it (raw=true).

    Confirmed live as a real ask for more -- a plain Canny edge outline
    was "not bad, but... make it cooler." Three additions, each a real
    computer-vision-HUD convention, not just a color filter: thicker
    dilated edges so lines read clearly at typical display size; a glow/
    bloom pass (blur the edge layer, add it back additively -- the
    standard trick for that glowing-line sci-fi look); and a genuine
    object-detection annotation -- a corner-bracket targeting reticle on
    any face the bundled Haar cascade detects, plus faint scanlines for
    texture."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, 60, 160)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)

    canvas = np.zeros_like(frame)
    canvas[edges > 0] = _VISION_EDGE_COLOR_BGR

    glow = cv2.GaussianBlur(canvas, (0, 0), sigmaX=4)
    canvas = cv2.add(canvas, glow)

    for (x, y, w, h) in _detect_faces(gray):
        _draw_target_reticle(canvas, x, y, w, h)

    canvas[::3, :] = (canvas[::3, :] * 0.75).astype(np.uint8)  # faint scanlines
    return canvas


def _take_screenshot(args: dict) -> bytes:
    import pyautogui

    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _capture_camera(args: dict) -> bytes | str:
    """One-shot webcam frame, same bytes-result pipeline as the screenshot
    tool (Orchestrator._on_tool_call base64-encodes bytes as an image
    content block the model can actually see) -- what's RETURNED (and so
    what the model sees) is always the real frame, so it can actually
    answer questions about it accurately. CONFIRM-tier, unlike the
    screenshot tool -- this captures the physical room/person rather than
    the screen, which is a meaningfully more sensitive thing to send off
    to a frontier model without the user explicitly saying yes each time.

    What's DISPLAYED in the console is a separate choice: publishes its
    own show_modal event here (stylized by default -- see _stylize_vision
    -- or the real frame when args["raw"] is true), same large show
    window fetch_image/show_website use -- confirmed live as a real gap:
    a first version routed this to the small incidental-capture strip
    instead, and "show me what you see on the camera" is unambiguously a
    "show me" request, same family as "show me a picture of X." This is
    instead of letting Orchestrator._on_tool_call auto-display the raw
    bytes this returns, which is deliberately skipped for this tool --
    see its comment."""
    import cv2

    cap = cv2.VideoCapture(0)
    try:
        if not cap.isOpened():
            return "error: no camera available (failed to open device 0)"
        # Discard the first couple of frames -- most webcams need a beat to
        # auto-adjust exposure/white-balance after opening, so frame 0 is
        # often too dark or washed out to be useful.
        for _ in range(3):
            cap.read()
        ok, frame = cap.read()
        if not ok:
            return "error: camera opened but failed to capture a frame"
    finally:
        cap.release()

    raw = bool(args.get("raw"))
    display_frame = frame if raw else _stylize_vision(frame)
    ok, encoded_display = cv2.imencode(".jpg", display_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        ui_events.publish({
            "type": "show_modal",
            "kind": "image",
            "title": "What Argus sees" if raw else "What Argus sees (computer vision)",
            "image": base64.b64encode(encoded_display.tobytes()).decode("ascii"),
        })

    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return "error: failed to encode captured frame"
    return encoded.tobytes()


_MAX_UI_ELEMENTS = 60  # a dense window (a spreadsheet, a long form) shouldn't produce an unusably long list
_LABELED_CONTROL_TYPES = ("Button", "Edit", "CheckBox", "MenuItem", "Hyperlink", "ListItem", "TabItem", "RadioButton")


def _list_ui_elements(args: dict) -> str:
    """Enumerates real, labeled clickable elements from the OS accessibility
    tree (Windows UI Automation, via pywinauto) for the active window --
    exact bounding boxes and names straight from the app itself, instead
    of guessing pixel coordinates off a screenshot.

    Confirmed live as a real gap: pure screenshot-and-guess clicking
    produced a malformed click call (x parsed as a string containing part
    of the JSON) and burned 20+ tool-call iterations failing to find a
    small delete button in a webmail UI. This is the "Set-of-Mark"
    pattern used by GUI-agent research generally (label real targets,
    pick one, don't guess coordinates) -- but doesn't need a vision model
    or GPU at all, since Windows already exposes this tree for free.

    Falls back to telling the model to use take_screenshot + click when
    the foreground app doesn't expose a usable tree (canvas-rendered
    apps, some games, some custom-drawn UI)."""
    from pywinauto import Desktop

    try:
        window = Desktop(backend="uia").window(active_only=True)
        window.set_focus()
    except Exception as e:
        return f"error: couldn't find the active window: {type(e).__name__}: {e}"

    try:
        elements = window.descendants()
    except Exception as e:
        return f"error: couldn't read UI elements from this window: {type(e).__name__}: {e}"

    lines = []
    for el in elements:
        if len(lines) >= _MAX_UI_ELEMENTS:
            break
        try:
            if not el.is_visible() or not el.is_enabled():
                continue
            rect = el.rectangle()
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            name = (el.window_text() or "").strip()
            control_type = el.element_info.control_type
            if not name and control_type not in _LABELED_CONTROL_TYPES:
                continue  # unlabeled, non-actionable noise (generic panes/groups)
            cx, cy = rect.mid_point()
            lines.append(f'[{len(lines) + 1}] {control_type} "{name}" at ({cx}, {cy})')
        except Exception:
            continue  # one flaky element must not break the whole listing

    if not lines:
        return (
            "No labeled UI elements found in the active window -- it may not expose a "
            "standard accessibility tree (common in canvas-rendered apps/games). Fall back "
            "to take_screenshot + click with pixel coordinates."
        )
    return "\n".join(lines)


def _list_windows(args: dict) -> str:
    import pygetwindow as gw

    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
    return "\n".join(titles) if titles else "(no visible windows)"


def _click(args: dict) -> str:
    import pyautogui

    try:
        x, y = int(args["x"]), int(args["y"])
    except (KeyError, TypeError, ValueError):
        # A malformed x/y (e.g. one coordinate string like "258, 60" instead
        # of two separate ints) would otherwise hit pyautogui's fallback
        # that tries to locate an *image* on screen -- a cryptic
        # FileNotFoundError that doesn't tell the model what actually went
        # wrong. This gives it something it can actually self-correct from.
        return f"error: x and y must both be integers, got x={args.get('x')!r} y={args.get('y')!r}"

    if args.get("double"):
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y)
    return f"clicked at ({x}, {y})"


def _scroll(args: dict) -> str:
    import pyautogui

    try:
        amount = int(args["amount"])
    except (KeyError, TypeError, ValueError):
        return f"error: amount must be an integer (positive = up, negative = down), got {args.get('amount')!r}"

    x, y = args.get("x"), args.get("y")
    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y))
    pyautogui.scroll(amount)
    return f"scrolled {amount}"


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

capture_camera_tool = Tool(
    name="capture_camera",
    description=(
        "Takes a single photo from the webcam so you can see what's physically in front of "
        "the computer -- the room, the user, an object they're holding up, etc -- and describe "
        "it. Distinct from take_screenshot (that's the screen, this is the camera). By default "
        "the console displays a stylized computer-vision-style rendering of the frame, NOT the "
        "literal photo (the raw photo is never shown unless raw=true is explicitly requested) "
        "-- you still always see and analyze the real frame either way, this only changes what "
        "the user sees on screen. Requires the user's confirmation each time since it captures "
        "the physical space, not just the screen."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "raw": {
                "type": "boolean",
                "description": "Show the literal captured photo instead of the default stylized rendering -- only when the user explicitly asks to see the raw/actual photo.",
            },
        },
    },
    tier=PermissionTier.CONFIRM,
    handler=_capture_camera,
)

list_windows_tool = Tool(
    name="list_windows",
    description="List the titles of all open windows.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_windows,
)

list_ui_elements_tool = Tool(
    name="list_ui_elements",
    description=(
        "Lists real, labeled clickable elements (buttons, links, fields, menu items) in the "
        "active window with their exact coordinates, read directly from the OS accessibility "
        "tree -- no guessing. Prefer this over take_screenshot+click for interacting with a "
        "specific control (finding a button, a link, a form field) in a normal desktop app or "
        "browser -- it's faster and far more reliable than pixel-coordinate guessing off a "
        "screenshot. Falls back to take_screenshot if it reports no elements found (some "
        "canvas-rendered apps/games don't expose this tree). After listing, click using the "
        "click tool with the (x, y) shown for the element you want."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_ui_elements,
)

click_tool = Tool(
    name="click",
    description=(
        "Click at pixel coordinates (x, y) on screen. Prefer list_ui_elements first to get "
        "exact coordinates for a specific control rather than guessing from a screenshot."
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
    repeatable=True,
    group="desktop_control",
    handler=_click,
)

scroll_tool = Tool(
    name="scroll",
    description=(
        "Scrolls the page/window at the current (or given) cursor position. Positive amount "
        "scrolls up, negative scrolls down -- e.g. -400 to scroll down a webpage or email to "
        "find content below the fold. Take a screenshot after to see the new state before "
        "clicking anything. Without this, anything below the initial viewport (a link near "
        "the bottom of an email, an item further down a list) was simply unreachable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "amount": {"type": "integer", "description": "Scroll amount; positive = up, negative = down."},
            "x": {"type": "integer", "description": "Optional: move here first, then scroll (defaults to current cursor position)."},
            "y": {"type": "integer"},
        },
        "required": ["amount"],
    },
    tier=PermissionTier.CONFIRM,
    repeatable=True,
    group="desktop_control",
    handler=_scroll,
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
    repeatable=True,
    group="desktop_control",
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
    repeatable=True,
    group="desktop_control",
    handler=_press_key,
)

open_app_tool = Tool(
    name="open_app",
    description=(
        "Open an application by name (e.g. 'notepad', 'calc'), a file/folder path, "
        "or a URL (opens in the default browser, e.g. 'https://youtube.com')."
    ),
    input_schema={
        "type": "object",
        "properties": {"app": {"type": "string"}},
        "required": ["app"],
    },
    tier=PermissionTier.CONFIRM,
    repeatable=True,
    group="desktop_control",
    handler=_open_app,
)
