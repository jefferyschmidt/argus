from pathlib import Path

from argus.ingest import UnsupportedFileType, ingest_file
from argus.tools.base import PermissionTier, Tool


def _ingest_document(args: dict) -> str:
    path = Path(args["path"]).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return f"error: {args['path']} does not exist"
    if not path.is_file():
        return f"error: {args['path']} is not a file"

    try:
        chunk_count = ingest_file(path)
    except UnsupportedFileType as e:
        return f"error: {e}"
    except Exception as e:
        return f"error: failed to read/ingest {path.name}: {type(e).__name__}: {e}"

    if chunk_count == 0:
        return f"{path.name} had no extractable text -- nothing stored."
    return f"Read and stored {path.name} into memory ({chunk_count} chunk(s)). Ask me about it anytime."


ingest_document_tool = Tool(
    name="ingest_document",
    description=(
        "Reads a PDF, .txt, or .md file and stores its contents into long-term semantic "
        "memory so it can be recalled in later conversation -- use this when the user asks "
        "you to 'read', 'remember', or 'learn' a specific document/file. This is separate "
        "from read_file (which just returns text for the current turn, nothing is "
        "remembered) -- prefer this one whenever the point is building lasting knowledge, "
        "not a one-off lookup."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to a .pdf, .txt, or .md file."}},
        "required": ["path"],
    },
    tier=PermissionTier.ALLOW,
    handler=_ingest_document,
)
