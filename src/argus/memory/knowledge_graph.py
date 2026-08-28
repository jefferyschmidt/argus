import sqlite3


class KnowledgeGraphStore:
    """Personal knowledge graph (README dream/stretch item): not just flat
    memory, but a structured map of people/projects/relationships built
    over time, so relational questions ("who else is on the Coshocton line
    besides Jason?") are actually answerable -- semantic memory search
    finds documents *about* a topic, but has no notion of a specific typed
    relationship between two named things, which is what this adds.

    Deliberately a plain subject/predicate/object triple store, not a real
    graph database -- for a personal assistant's scale (hundreds to low
    thousands of facts, not millions), a triple store with two indexes
    covers both traversal directions ("what does X relate to" and "what
    relates to X") without the operational overhead of an actual graph
    engine. Facts are opportunistically added by the model during normal
    conversation via the remember_relationship tool, not extracted by a
    separate pipeline -- the model already has the context to know when
    something's a genuinely reusable fact worth structuring, the same way
    core memory proposals already work."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, subject: str, predicate: str, object_: str) -> int:
        subject, predicate, object_ = subject.strip(), predicate.strip(), object_.strip()
        cur = self.conn.execute(
            "INSERT INTO kg_facts (subject, predicate, object) VALUES (?, ?, ?) "
            "ON CONFLICT(subject, predicate, object) DO UPDATE SET created_at = created_at "
            "RETURNING id",
            (subject, predicate, object_),
        )
        row = cur.fetchone()
        self.conn.commit()
        return row[0]

    def query(self, entity: str, limit: int = 25) -> list[sqlite3.Row]:
        """Facts where entity appears as either subject or object (a
        case-insensitive substring match, not exact -- "Coshocton" should
        still find "Coshocton line" without the caller needing the exact
        stored string), most recent first."""
        pattern = f"%{entity.strip()}%"
        return self.conn.execute(
            "SELECT id, subject, predicate, object FROM kg_facts "
            "WHERE subject LIKE ? COLLATE NOCASE OR object LIKE ? COLLATE NOCASE "
            "ORDER BY created_at DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()

    def list_all(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, subject, predicate, object FROM kg_facts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def delete(self, fact_id: int) -> None:
        self.conn.execute("DELETE FROM kg_facts WHERE id = ?", (fact_id,))
        self.conn.commit()
