from datetime import datetime

from argus.llm.base import Tier
from argus.memory.semantic import SemanticStore
from argus.tools.base import PermissionTier, Tool

_EXTRACTION_PROMPT = """Look at this photo of a document or receipt. Extract
what's actually on it: what kind of document it is, who it's from/the
vendor, the date if visible, the total amount if it's a receipt/invoice,
and any other genuinely useful specifics (key line items, an account
number, a due date). Write it as a short, clear paragraph a person could
search for later (e.g. "Receipt from Trader Joe's, $47.32, produce and
groceries, dated..."), not a bulleted form. If the photo doesn't actually
show a readable document, say so plainly instead of guessing."""


def _build_scan_document(router) -> Tool:
    def _scan_document(args: dict) -> str:
        import cv2

        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return "error: no camera available (failed to open device 0)"
            for _ in range(3):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                return "error: camera opened but failed to capture a frame"
        finally:
            cap.release()

        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return "error: failed to encode captured frame"
        image_bytes = encoded.tobytes()

        try:
            result = router.complete_with_image(image_bytes, _EXTRACTION_PROMPT, tier=Tier.FAST)
        except Exception as e:
            return f"error: failed reading the document: {type(e).__name__}: {e}"

        extracted = result.text.strip()
        if not extracted:
            return "Couldn't read anything useful off of that -- try holding it closer/steadier to the camera."

        now = datetime.now()
        store = SemanticStore()
        store.add(
            doc_id=f"scan-{now.strftime('%Y%m%d%H%M%S%f')}",
            text=extracted,
            metadata={"source": "document_scan", "scanned_at": now.isoformat()},
        )
        return extracted

    return Tool(
        name="scan_document",
        description=(
            "Captures a photo via the webcam of a document/receipt the user is holding up and "
            "extracts what it says (vendor, amount, date, key details), storing it into "
            "long-term memory so questions like 'how much did I spend at X' or 'when's that "
            "bill due' are answerable later. Use this when the user says something like 'scan "
            "this' or 'here's a receipt' while holding something up -- distinct from "
            "capture_camera (that's for looking at the room/a person, this is specifically for "
            "reading and remembering a document)."
        ),
        input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM,  # captures the physical room, same sensitivity as capture_camera
        handler=_scan_document,
    )
