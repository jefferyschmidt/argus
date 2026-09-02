"""PRD.md §8 (Phase E-compose). compose() is the ONLY way a document
gets written -- pure rendering + atomic write + spine emission. No LLM
call of its own: drafting the content is "task execution" (the allowed
LLM-use category PRD §0 lists separately from document composition
itself) -- by the time a DocumentSpec reaches this function its
sections are already finished text, the same shape write_file_tool
already takes content in.

**Nothing in this module sends, posts, or uploads anything.** Every
import here is stdlib plus argus.config/argus.spine -- nothing that can
open a network connection. Long compositions run as a Phase I task, not
inline: that's achieved simply by *how* compose_document gets called
(directly in a normal turn for something quick, or from inside an
autonomous task's own tool loop for something that needs gathering
first) -- this module has no separate "is this long" branch to get
wrong."""

import html as html_lib
import string
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from argus.config import settings
from argus.spine.observation import Observation
from argus.spine.store import SpineStore

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_EXTENSIONS = {"markdown": "md", "html": "html"}


@dataclass
class DocumentSection:
    body: str
    heading: str = ""


@dataclass
class DocumentSpec:
    title: str
    format: str  # "markdown" | "html"
    sections: list[DocumentSection] = field(default_factory=list)
    # Optional: appends a table of matching spine observations as a final
    # section -- {"kinds": [...], "since": <epoch float>, "limit": int}.
    # Only used when a SpineStore is actually passed to compose().
    spine_query: dict | None = None


class UnsupportedFormat(ValueError):
    pass


def compose(spec: DocumentSpec, snapshot=None, spine: SpineStore | None = None) -> Path:
    """Renders `spec` to disk under settings.data_dir/documents and
    returns the path. Atomic: writes to a temp file in the same
    directory first and only replaces the real path once the full
    content is written, so a composition that dies partway -- an
    exception while rendering, a task budget tripping elsewhere, a
    process kill -- leaves no partial file at the returned/expected path
    (§8 acceptance). Emits exactly one document.composed observation
    when a spine is given."""
    if spec.format not in _EXTENSIONS:
        raise UnsupportedFormat(f"Unsupported format {spec.format!r} -- must be 'markdown' or 'html'")

    generated_at = snapshot.now.isoformat() if snapshot is not None else time.strftime("%Y-%m-%d %H:%M:%S")

    sections = list(spec.sections)
    if spec.spine_query and spine is not None:
        sections.append(_spine_query_section(spec.spine_query, spine, spec.format))

    body = _render_sections(sections, spec.format)
    text = _render_template(spec.format, title=spec.title, generated_at=generated_at, body=body)

    documents_dir = settings.data_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    ext = _EXTENSIONS[spec.format]
    final_path = documents_dir / f"{_slugify(spec.title)}-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}"
    tmp_path = documents_dir / f".{final_path.name}.tmp"

    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    if spine is not None:
        spine.record(Observation(
            source="compose", kind="document.composed", ts=time.time(), subject=spec.title,
            payload={"path": str(final_path), "format": spec.format},
        ))

    return final_path


def _render_sections(sections: list[DocumentSection], fmt: str) -> str:
    if fmt == "html":
        parts = []
        for section in sections:
            if section.heading:
                parts.append(f"<h2>{html_lib.escape(section.heading)}</h2>")
            parts.append(f"<p>{html_lib.escape(section.body)}</p>")
        return "\n".join(parts)
    # markdown
    parts = []
    for section in sections:
        if section.heading:
            parts.append(f"## {section.heading}\n")
        parts.append(f"{section.body}\n")
    return "\n".join(parts)


def _spine_query_section(query: dict, spine: SpineStore, fmt: str) -> DocumentSection:
    observations = spine.query(
        kinds=query.get("kinds"), since=query.get("since"), until=query.get("until"),
        limit=query.get("limit", 50),
    )
    if not observations:
        return DocumentSection(heading="Observations", body="(none)")
    if fmt == "html":
        rows = "".join(
            f"<tr><td>{html_lib.escape(o.kind)}</td><td>{html_lib.escape(o.subject or '')}</td></tr>"
            for o in observations
        )
        body = f"<table><tr><th>Kind</th><th>Subject</th></tr>{rows}</table>"
    else:
        lines = [f"- {o.kind}" + (f" -- {o.subject}" if o.subject else "") for o in observations]
        body = "\n".join(lines)
    return DocumentSection(heading="Observations", body=body)


def _render_template(fmt: str, *, title: str, generated_at: str, body: str) -> str:
    ext = _EXTENSIONS[fmt]
    template_path = _TEMPLATES_DIR / f"document.{ext}.tmpl"
    template = string.Template(template_path.read_text(encoding="utf-8"))
    return template.safe_substitute(title=title, generated_at=generated_at, body=body)


def _slugify(title: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "document"
