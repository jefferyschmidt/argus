from argus.memory.knowledge_graph import KnowledgeGraphStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool


def _remember_relationship(args: dict) -> str:
    subject, predicate, object_ = args["subject"], args["predicate"], args["object"]
    if not subject.strip() or not predicate.strip() or not object_.strip():
        return "error: subject, predicate, and object must all be non-empty"
    conn = get_connection()
    try:
        KnowledgeGraphStore(conn).add(subject, predicate, object_)
    finally:
        conn.close()
    return f'Noted: "{subject.strip()}" {predicate.strip()} "{object_.strip()}".'


def _query_relationships(args: dict) -> str:
    entity = args.get("entity", "").strip()
    if not entity:
        return "error: entity can't be empty"
    conn = get_connection()
    try:
        rows = KnowledgeGraphStore(conn).query(entity)
    finally:
        conn.close()
    if not rows:
        return f'No stored facts mention "{entity}".'
    return "\n".join(f'"{r["subject"]}" {r["predicate"]} "{r["object"]}"' for r in rows)


remember_relationship_tool = Tool(
    name="remember_relationship",
    description=(
        "Stores a structured fact about a relationship between two things (people, projects, "
        "places, anything) as a subject-predicate-object triple, e.g. subject='Jason', "
        "predicate='works on', object='the Coshocton line'. Use this whenever the user tells "
        "you something relational worth remembering long-term -- who's connected to what, who "
        "manages what, what belongs to what -- so later relational questions (\"who else is on "
        "the Coshocton line?\") are actually answerable via query_relationships, not just "
        "similarity search over conversation text. Write predicate as a short natural verb "
        "phrase ('works on', 'manages', 'is married to', 'lives in'), not a code-style key."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string", "description": "Short verb phrase, e.g. 'works on', 'manages'."},
            "object": {"type": "string"},
        },
        "required": ["subject", "predicate", "object"],
    },
    tier=PermissionTier.ALLOW,
    handler=_remember_relationship,
)

query_relationships_tool = Tool(
    name="query_relationships",
    description=(
        "Looks up stored relationship facts (from remember_relationship) that mention a given "
        "entity, in either direction (as subject or object) -- use this for relational "
        "questions like 'who else works on X' or 'what does Y manage', not semantic_search-"
        "style questions about general conversation history."
    ),
    input_schema={
        "type": "object",
        "properties": {"entity": {"type": "string", "description": "The person/project/thing to look up."}},
        "required": ["entity"],
    },
    tier=PermissionTier.ALLOW,
    handler=_query_relationships,
)
