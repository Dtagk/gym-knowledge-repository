import re
import sqlite3
from pydantic import BaseModel
from typing import Optional

import instructor
import lancedb
from gliner import GLiNER
from openai import OpenAI

from config.extraction_schema import Entity, Extraction, Relation, TechniqueCue
from yt_kg.db import init_db, utcnow, record_failure

_LABELS = [
    "fitness exercise or movement",
    "muscle group or body part",
    "scientific study or research paper",
    "fitness equipment or machine",
    "training technique or programming method",
    "fitness trainer, researcher or athlete",
    "gym, company or organization",
    "nutritional concept or supplement",
]
_LABEL_MAP = {
    "fitness exercise or movement": "Method",
    "muscle group or body part": "Concept",
    "scientific study or research paper": "Paper",
    "fitness equipment or machine": "Tool",
    "training technique or programming method": "Method",
    "fitness trainer, researcher or athlete": "Person",
    "gym, company or organization": "Organization",
    "nutritional concept or supplement": "Concept",
}
_PRONOUNS = {"i", "me", "we", "us", "you", "he", "she", "they", "them", "it", "my", "your", "our"}

_gliner_model: Optional[GLiNER] = None


def _gliner() -> GLiNER:
    global _gliner_model
    if _gliner_model is None:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _gliner_model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1", map_location=device)
    return _gliner_model


_llm = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)


class _LLMRelation(BaseModel):
    subject: str = ""
    predicate: str = ""
    object: str = ""
    evidence: str = ""


class _Relations(BaseModel):
    relations: list[_LLMRelation]


_VALID_KINDS = {"mistake", "cue", "setup", "tempo", "breathing", "range-of-motion"}


class _LLMCue(BaseModel):
    exercise: str = ""
    cue: str = ""
    kind: str = "cue"
    evidence: str = ""


class _Cues(BaseModel):
    cues: list[_LLMCue]


def _init_extractions_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_extractions (
            chunk_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            extraction_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _extract_chunk(text: str) -> Extraction:
    spans = _gliner().predict_entities(text, _LABELS, threshold=0.4)
    seen: set[tuple[str, str]] = set()
    entities: list[Entity] = []
    for s in spans:
        if s["text"].lower().strip() in _PRONOUNS:
            continue
        key = (s["text"].lower(), s["label"])
        if key not in seen:
            seen.add(key)
            entities.append(Entity(name=s["text"], type=_LABEL_MAP[s["label"]], description=""))

    # Relations: LLM only when ≥2 entities found
    relations: list[Relation] = []
    if len(entities) >= 2:
        names = [e.name for e in entities]
        prompt = (
            f"Entities found in this fitness transcript excerpt: {names}\n\n"
            f"{text}\n\n"
            f"Return JSON with a 'relations' array. Each item has: "
            f"subject, predicate, object (from the entity list), evidence (exact quote from text)."
        )
        try:
            r = _llm.chat.completions.create(
                model="qwen2.5-coder:7b",  # ponytail: base qwen2.5:7b better for NL, switch if relations quality suffers
                messages=[{"role": "user", "content": prompt}],
                response_model=_Relations,
                max_retries=1,
                timeout=60.0,
            )
            relations = [
                Relation(subject=lr.subject, predicate=lr.predicate, object=lr.object, evidence=lr.evidence)
                for lr in r.relations
                if lr.subject and lr.predicate and lr.object
            ]
        except Exception as exc:
            print(f"[extract] relations failed: {exc}")

    # Technique cues: LLM pass, gated on ≥1 exercise (Method) entity. Many
    # technique passages discuss a single movement, so the ≥2 gate used for
    # relations would miss them.
    cues: list[TechniqueCue] = []
    exercise_names = [e.name for e in entities if e.type == "Method"]
    if exercise_names:
        prompt = (
            f"Exercises mentioned in this fitness transcript excerpt: {exercise_names}\n\n"
            f"{text}\n\n"
            f"Extract technique coaching points for these exercises. Return JSON with a "
            f"'cues' array. Each item has: exercise (from the list above), cue (the "
            f"technique note or common mistake, one sentence), kind (one of: mistake, "
            f"cue, setup, tempo, breathing, range-of-motion), evidence (exact quote). "
            f"Only include cues actually stated in the text; return an empty array if none."
        )
        try:
            c = _llm.chat.completions.create(
                model="qwen2.5-coder:7b",  # ponytail: candidate for base qwen2.5:7b — see eval harness
                messages=[{"role": "user", "content": prompt}],
                response_model=_Cues,
                max_retries=1,
                timeout=60.0,
            )
            cues = [
                TechniqueCue(
                    exercise=lc.exercise,
                    cue=lc.cue,
                    kind=lc.kind if lc.kind in _VALID_KINDS else "cue",
                    evidence=lc.evidence,
                )
                for lc in c.cues
                if lc.exercise and lc.cue
            ]
        except Exception as exc:
            print(f"[extract] cues failed: {exc}")

    return Extraction(entities=entities, relations=relations, cues=cues)


def extract() -> None:
    conn = init_db()
    _init_extractions_table(conn)

    rows = conn.execute(
        "SELECT video_id FROM videos WHERE chunked_at IS NOT NULL AND extracted_at IS NULL AND skipped = 0"
    ).fetchall()

    db = lancedb.connect("data/vectors.lance")
    tbl = db.open_table("chunks")

    existing_ids = {
        r[0] for r in conn.execute("SELECT chunk_id FROM raw_extractions").fetchall()
    }

    for row in rows:
        video_id = row["video_id"]

        if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id):
            raise ValueError(f"Unexpected video_id format: {video_id!r}")

        try:
            chunks = tbl.search([0.0] * 384).where(
                f"video_id = '{video_id}'", prefilter=True
            ).limit(100000).to_list()
        except Exception:
            try:
                import pandas as pd  # noqa: F401
                chunks = tbl.to_pandas().query(f"video_id == '{video_id}'").to_dict("records")
            except Exception:
                all_chunks = tbl.search([0.0] * 384).limit(100000).to_list()
                chunks = [c for c in all_chunks if c.get("video_id") == video_id]

        if not chunks:
            conn.execute(
                "UPDATE videos SET extracted_at = ? WHERE video_id = ?",
                (utcnow(), video_id),
            )
            conn.commit()
            continue

        failures = 0

        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id in existing_ids:
                continue

            try:
                result = _extract_chunk(chunk["text"])
                conn.execute(
                    "INSERT OR REPLACE INTO raw_extractions (chunk_id, video_id, extraction_json, created_at) VALUES (?, ?, ?, ?)",
                    (chunk_id, video_id, result.model_dump_json(), utcnow()),
                )
                conn.commit()
                existing_ids.add(chunk_id)
            except Exception as exc:
                print(f"[extract] chunk {chunk_id} failed: {exc}")
                failures += 1

        failure_rate = failures / len(chunks) if chunks else 0.0
        if failure_rate > 0.03:
            record_failure(conn, video_id, "extract", f"{failures}/{len(chunks)} chunks failed extraction")
        else:
            conn.execute(
                "UPDATE videos SET extracted_at = ?, last_error = NULL, error_stage = NULL WHERE video_id = ?",
                (utcnow(), video_id),
            )
        conn.commit()


if __name__ == "__main__":
    extract()
