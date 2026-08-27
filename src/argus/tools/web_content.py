import io
import urllib.request

from argus.tools.base import PermissionTier, Tool

_MAX_BYTES = 10_000_000  # 10MB
_TIMEOUT_SECONDS = 10


def _fetch_image(args: dict) -> bytes | str:
    """Downloads an image from a direct URL and normalizes it to PNG so it
    displays consistently through the same image pipeline as take_screenshot
    (tool results that are raw bytes become an actual image the model can
    see and the UI can render, not just a text description)."""
    url = args["url"]
    if not url.startswith(("http://", "https://")):
        return "error: only http/https URLs are supported"

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ArgusAssistant/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return f"error: URL did not return an image (content-type: {content_type or 'unknown'})"
            data = resp.read(_MAX_BYTES + 1)
    except Exception as e:
        return f"error: failed to fetch image: {type(e).__name__}: {e}"

    if len(data) > _MAX_BYTES:
        return "error: image too large (over 10MB)"
    if not data:
        return "error: URL returned an empty response"

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        return f"error: could not decode image data: {type(e).__name__}: {e}"


fetch_image_tool = Tool(
    name="fetch_image",
    description=(
        "Download and display an image from a direct image URL (e.g. a .jpg/.png link, or an "
        "image URL found via web search results). Use this whenever the user asks to see a "
        "picture of something -- search for it first if you don't already have a direct image "
        "URL (e.g. a real estate listing photo for 'a picture of my house', a product photo, "
        "a map/satellite image link), then fetch it here so it actually displays."
    ),
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    tier=PermissionTier.ALLOW,
    handler=_fetch_image,
)
