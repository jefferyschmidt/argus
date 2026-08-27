import io
import urllib.request

from argus.tools.base import PermissionTier, Tool

_MAX_DOWNLOAD_BYTES = 15_000_000  # cap on the raw download, before re-encoding
_TIMEOUT_SECONDS = 10
_MAX_DIMENSION = 1568  # Anthropic downsamples above this anyway; no point sending more
# Anthropic's image limit is 10MB on the BASE64-encoded content, which is
# ~4/3 the size of the raw bytes -- stay comfortably under that with margin,
# not right at the edge.
_MAX_OUTPUT_BYTES = 4_500_000


def _fetch_image(args: dict) -> bytes | str:
    """Downloads an image from a direct URL and normalizes it to a bounded
    JPEG so it displays consistently through the same image pipeline as
    take_screenshot (tool results that are raw bytes become an actual image
    the model can see and the UI can render, not just a text description).

    Re-encoding to lossless PNG was the original approach, but that can
    *inflate* a compressed source photo past Anthropic's 10MB image limit
    and fail the entire turn (confirmed live: an 11MB PNG from an ~8MB
    source JPEG). JPEG is the right format for downloaded photos anyway --
    PNG's lossless behavior matters for screenshots (sharp text/UI), not
    photographic content."""
    url = args["url"]
    if not url.startswith(("http://", "https://")):
        return "error: only http/https URLs are supported"

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ArgusAssistant/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return f"error: URL did not return an image (content-type: {content_type or 'unknown'})"
            data = resp.read(_MAX_DOWNLOAD_BYTES + 1)
    except Exception as e:
        return f"error: failed to fetch image: {type(e).__name__}: {e}"

    if len(data) > _MAX_DOWNLOAD_BYTES:
        return "error: image too large (over 15MB)"
    if not data:
        return "error: URL returned an empty response"

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        return f"error: could not decode image data: {type(e).__name__}: {e}"

    if max(img.size) > _MAX_DIMENSION:
        img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        encoded = buf.getvalue()
        if len(encoded) <= _MAX_OUTPUT_BYTES:
            return encoded
    return encoded  # smallest attempt, still returned rather than silently dropped


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
