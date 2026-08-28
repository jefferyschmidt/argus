"""Second-brain ingestion: turn a PDF/txt/md file into searchable long-term
memory. Chunked and stored through the same SemanticStore conversation
recall already uses (memory/manager.py's build_context does a plain
semantic.search over everything in the collection, source included), so
anything ingested here is automatically recallable in normal conversation
with zero changes to the recall path.

Chunked rather than stored as one giant document: Chroma's default embedder
(all-MiniLM-L6-v2) has a real input length it silently truncates past, so a
whole PDF as a single document would only ever be searchable by its first
~256 tokens. Fixed-size character chunks with overlap are crude compared to
sentence/paragraph-aware splitting, but simple, dependency-free, and good
enough for "did I read something about X" style recall.
"""

import hashlib
import logging
from pathlib import Path

from argus.memory.semantic import SemanticStore

log = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}
_CHUNK_CHARS = 1500
_CHUNK_OVERLAP = 200


class UnsupportedFileType(Exception):
    pass


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if ext in (".txt", ".md"):
        return path.read_text(errors="replace")
    raise UnsupportedFileType(f"unsupported file type: {ext} (supported: {sorted(_SUPPORTED_EXTENSIONS)})")


def _chunk(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_CHARS
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - _CHUNK_OVERLAP
    return chunks


def ingest_file(path: Path, store: SemanticStore | None = None) -> int:
    """Extracts, chunks, and stores a file's text as semantic memory.
    Returns the number of chunks stored (0 for an empty/unreadable file).
    Re-ingesting the same file overwrites its old chunks rather than
    duplicating them -- doc ids are deterministic (path + chunk index), and
    SemanticStore.add uses upsert."""
    store = store or SemanticStore()
    text = _extract_text(path)
    chunks = _chunk(text)

    # Stable across re-ingests of the same path, distinct across different
    # files that happen to share a name in different folders.
    path_hash = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]

    for i, chunk in enumerate(chunks):
        store.add(
            doc_id=f"ingest-{path_hash}-{i}",
            text=chunk,
            metadata={"source": "ingest", "path": str(path), "filename": path.name, "chunk": i},
        )
    log.info("Ingested %s: %d chunk(s)", path, len(chunks))
    return len(chunks)
