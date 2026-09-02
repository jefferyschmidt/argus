"""PRD.md §8. compose_document -- the only way to produce a document from
a normal turn or from inside an autonomous task's own tool loop. Never
sends, posts, or uploads anything (see argus/compose/compose.py's
docstring); the file is saved locally for the user to send themselves.
Spends real money if the calling model drafted the content first, so
this is CONFIRM tier -- same reasoning as start_task."""

from argus.compose.compose import DocumentSection, DocumentSpec, compose
from argus.tools.base import PermissionTier, Tool


def _build_compose_document(spine) -> Tool:
    def handler(args: dict) -> str:
        sections = [
            DocumentSection(heading=s.get("heading", ""), body=s["body"])
            for s in args.get("sections", [])
        ]
        spec = DocumentSpec(title=args["title"], format=args.get("format", "markdown"), sections=sections)
        path = compose(spec, spine=spine)
        return f"Document composed: {path}"

    return Tool(
        name="compose_document",
        description=(
            "Writes a finished document -- you provide the title and already-drafted "
            "section text -- to disk as Markdown or HTML. Never sends, posts, or uploads it "
            "anywhere; it's saved locally for the user to read and send themselves. Spends "
            "real money if you drafted the content with an LLM call first, so this requires "
            "confirmation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The document's title."},
                "format": {"type": "string", "enum": ["markdown", "html"], "description": "Defaults to markdown."},
                "sections": {
                    "type": "array",
                    "description": "Already-written content, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["body"],
                    },
                },
            },
            "required": ["title", "sections"],
        },
        tier=PermissionTier.CONFIRM,
        handler=handler,
    )
