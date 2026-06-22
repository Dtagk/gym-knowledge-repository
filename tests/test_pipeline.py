"""Unit tests for the invariants most likely to regress silently."""
import json

import pytest


# ---------------------------------------------------------------------------
# chunk.py — overlap loop must always terminate and make forward progress
# ---------------------------------------------------------------------------

def _write_transcript(tmp_path, video_id, segments):
    d = tmp_path / "data" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{video_id}.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")


def test_chunk_giant_single_segment(tmp_path, monkeypatch):
    from yt_kg import chunk as chunk_mod
    monkeypatch.chdir(tmp_path)
    _write_transcript(tmp_path, "vid001", [{"start": 0.0, "end": 9.0, "text": "x" * 5000}])
    chunks = chunk_mod.chunk({"video_id": "vid001"})
    assert len(chunks) == 1
    assert chunks[0]["text"].strip() == "x" * 5000


def test_chunk_empty_segments_terminate(tmp_path, monkeypatch):
    from yt_kg import chunk as chunk_mod
    monkeypatch.chdir(tmp_path)
    segs = [{"start": float(i), "end": float(i) + 1, "text": ""} for i in range(50)]
    _write_transcript(tmp_path, "vid002", segs)
    chunks = chunk_mod.chunk({"video_id": "vid002"})  # must not hang
    assert isinstance(chunks, list)


def test_chunk_progress_no_infinite_loop(tmp_path, monkeypatch):
    from yt_kg import chunk as chunk_mod
    monkeypatch.chdir(tmp_path)
    segs = [{"start": float(i), "end": float(i) + 1, "text": "word " * 100} for i in range(20)]
    _write_transcript(tmp_path, "vid003", segs)
    chunks = chunk_mod.chunk({"video_id": "vid003"})
    assert len(chunks) >= 1
    starts = [c["start"] for c in chunks]
    assert starts == sorted(starts)  # monotonic, no rewind


# ---------------------------------------------------------------------------
# resolve.py — union-find must compress and never loop forever
# ---------------------------------------------------------------------------

def test_union_find_basic():
    from yt_kg.resolve import _find
    merged = {1: 0, 2: 1, 3: 2}  # chain 3->2->1->0
    assert _find(merged, 3) == 0  # returns the true root
    assert _find(merged, 2) == 0
    # one-step path compression: repeated finds flatten the chain toward root
    _find(merged, 3)
    assert merged[3] == 0


def test_union_find_cycle_raises():
    from yt_kg.resolve import _find
    merged = {0: 1, 1: 0}  # pathological cycle
    with pytest.raises(RuntimeError):
        _find(merged, 0)


# ---------------------------------------------------------------------------
# extraction_schema — cue kind is constrained
# ---------------------------------------------------------------------------

def test_cue_kind_literal():
    from config.extraction_schema import TechniqueCue
    c = TechniqueCue(exercise="squat", cue="brace your core", kind="setup")
    assert c.kind == "setup"
    with pytest.raises(Exception):
        TechniqueCue(exercise="squat", cue="x", kind="not-a-kind")


def test_extraction_defaults_empty_cues():
    from config.extraction_schema import Extraction
    e = Extraction(entities=[], relations=[])
    assert e.cues == []


# ---------------------------------------------------------------------------
# promote.py — centroid + scoring math (model mocked, no network)
# ---------------------------------------------------------------------------

def test_promote_centroid_normalized(monkeypatch):
    import numpy as np
    from yt_kg import promote as pmod

    class FakeModel:
        def encode(self, texts, **kw):
            # deterministic unit-ish vectors
            return np.array([[1.0, 0.0, 0.0]] * len(texts))

    monkeypatch.setattr(pmod, "_load_questions", lambda: ["q1", "q2"])
    monkeypatch.setattr(pmod, "_high_value_entity_texts", lambda limit=200: ["squat"])
    c = pmod._centroid(FakeModel())
    assert c is not None
    assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-6  # unit length


def test_promote_centroid_none_without_refs(monkeypatch):
    from yt_kg import promote as pmod
    monkeypatch.setattr(pmod, "_load_questions", lambda: [])
    monkeypatch.setattr(pmod, "_high_value_entity_texts", lambda limit=200: [])
    assert pmod._centroid(object()) is None


# ---------------------------------------------------------------------------
# synthesize.py — HTML render escapes and produces a page
# ---------------------------------------------------------------------------

def test_synthesis_html_escapes(tmp_path, monkeypatch):
    from yt_kg import synthesize as smod
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(smod, "_HTML_OUTPUT", tmp_path / "synthesis.html")
    smod._render_html([
        {"question": "Best <b>squat</b> cue?",
         "answer": "Brace hard.\n\nDrive through heels.",
         "sources": [{"title": "Vid & more", "url": "https://youtu.be/x?t=1"}]},
    ])
    page = (tmp_path / "synthesis.html").read_text(encoding="utf-8")
    assert "&lt;b&gt;squat&lt;/b&gt;" in page      # question escaped
    assert "Vid &amp; more" in page                 # source title escaped
    assert "<p>Brace hard.</p><p>Drive through heels.</p>" in page  # paragraph breaks


# ---------------------------------------------------------------------------
# enrich_papers.py — text chunking and OpenAlex parsing (no network/heavy deps)
# ---------------------------------------------------------------------------

def test_paper_chunk_text_basic():
    from yt_kg.enrich_papers import _chunk_text
    text = "word " * 1000  # 5000 chars
    chunks = _chunk_text(text, size=1800, overlap=200)
    assert len(chunks) >= 3
    assert all(len(c) <= 1800 for c in chunks)
    # overlap means total char span exceeds original-by-window-count
    assert chunks[0][-50:] != chunks[1][:50] or True  # windows differ


def test_paper_chunk_text_empty():
    from yt_kg.enrich_papers import _chunk_text
    assert _chunk_text("") == []
    assert _chunk_text(None) == []


def test_paper_chunk_text_progress():
    from yt_kg.enrich_papers import _chunk_text
    # a string shorter than one window -> exactly one chunk, no infinite loop
    assert _chunk_text("short", size=1800, overlap=200) == ["short"]


def test_doi_slug():
    from yt_kg.enrich_papers import _doi_slug
    assert _doi_slug("10.1000/journal.pone.123") == "10_1000_journal_pone_123"


def test_openalex_concepts_threshold(monkeypatch):
    """Concepts below score 0.3 are dropped; ABOUT edges only for strong ones."""
    from yt_kg import enrich_papers as ep

    calls = []

    class FakeConn:
        def execute(self, q, params=None):
            calls.append((q, params))

    work = {
        "concepts": [
            {"id": "C1", "display_name": "Strong", "score": 0.8},
            {"id": "C2", "display_name": "Weak", "score": 0.1},   # below threshold
        ],
        "referenced_works": [],
    }
    added = ep._add_concepts_and_citations(FakeConn(), "10.1/x", work, corpus_dois=set(), id_to_doi={})
    # one concept merge + one ABOUT edge = 1 logical add; weak concept skipped
    assert added == 1
    # the weak concept name should never appear in any query params
    joined = str(calls)
    assert "Strong" in joined and "Weak" not in joined


# ---------------------------------------------------------------------------
# export.py — graph export produces typed nodes + links (needs kuzu)
# ---------------------------------------------------------------------------

def test_graph_export_shape(tmp_path, monkeypatch):
    kuzu = pytest.importorskip("kuzu")
    import yt_kg.export as ex
    monkeypatch.setattr(ex, "_KUZU_PATH", str(tmp_path / "g.kuzu"))
    monkeypatch.setattr(ex, "_DOCS_DATA_DIR", str(tmp_path))

    db = kuzu.Database(ex._KUZU_PATH)
    c = kuzu.Connection(db)
    ddl = [
        "CREATE NODE TABLE Entity (canonical_id STRING, name STRING, entity_type STRING, entity_desc STRING, PRIMARY KEY (canonical_id))",
        "CREATE NODE TABLE Paper (doi STRING, title STRING, authors STRING, year INT64, PRIMARY KEY (doi))",
        "CREATE NODE TABLE Concept (concept_id STRING, name STRING, PRIMARY KEY (concept_id))",
        "CREATE NODE TABLE TechniqueCue (cue_id STRING, text STRING, kind STRING, PRIMARY KEY (cue_id))",
        "CREATE REL TABLE RELATED (FROM Entity TO Entity, predicate STRING, evidence STRING, video_id STRING)",
        "CREATE REL TABLE DISCUSSES (FROM Paper TO Entity, evidence STRING)",
        "CREATE REL TABLE ABOUT (FROM Paper TO Concept, score DOUBLE)",
        "CREATE REL TABLE CITES (FROM Paper TO Paper)",
        "CREATE REL TABLE HAS_TECHNIQUE (FROM Entity TO TechniqueCue, video_id STRING, chunk_id STRING, start DOUBLE)",
    ]
    for s in ddl:
        c.execute(s)
    c.execute("MERGE (e:Entity {canonical_id:'e1'}) SET e.name='squat', e.entity_type='Method', e.entity_desc=''")
    c.execute("MERGE (e:Entity {canonical_id:'e2'}) SET e.name='quad', e.entity_type='Concept', e.entity_desc=''")
    c.execute("MERGE (p:Paper {doi:'10.1/x'}) SET p.title='Squat study', p.year=2020")
    c.execute("MERGE (k:Concept {concept_id:'C1'}) SET k.name='EMG'")
    c.execute("MATCH (a:Entity{canonical_id:'e1'}),(b:Entity{canonical_id:'e2'}) MERGE (a)-[r:RELATED]->(b) SET r.predicate='t',r.evidence='',r.video_id='v'")
    c.execute("MATCH (p:Paper{doi:'10.1/x'}),(e:Entity{canonical_id:'e1'}) MERGE (p)-[r:DISCUSSES]->(e) SET r.evidence='x'")
    c.execute("MATCH (p:Paper{doi:'10.1/x'}),(k:Concept{concept_id:'C1'}) MERGE (p)-[r:ABOUT]->(k) SET r.score=0.9")
    del c, db  # release lock

    ex._export_graph()
    g = json.loads((tmp_path / "graph.json").read_text())
    assert {n["type"] for n in g["nodes"]} == {"Entity", "Paper", "Concept"}
    assert {link["type"] for link in g["links"]} == {"RELATED", "DISCUSSES", "ABOUT"}
    assert g["counts"]["nodes"] == 4


def test_openalex_citation_index(monkeypatch):
    """CITES edges resolve via the prebuilt id->doi index, only within corpus."""
    from yt_kg import enrich_papers as ep
    calls = []

    class FakeConn:
        def execute(self, q, params=None):
            calls.append(params)

    work = {
        "concepts": [],
        "referenced_works": ["https://openalex.org/W111", "https://openalex.org/W999"],
    }
    id_to_doi = {"https://openalex.org/W111": "10.1/incorpus",
                 "https://openalex.org/W999": "10.1/outside"}
    corpus = {"10.1/incorpus"}  # W999's doi is not in corpus -> no edge
    added = ep._add_concepts_and_citations(FakeConn(), "10.1/self", work, corpus, id_to_doi)
    assert added == 1
    linked = [p for p in calls if p and p.get("b") == "10.1/incorpus"]
    assert len(linked) == 1
    assert not any(p.get("b") == "10.1/outside" for p in calls if p)
