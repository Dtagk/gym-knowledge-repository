"""Extraction eval harness.

Measures precision/recall/F1 of the extract stage against a hand-labeled gold
set, broken out by entities, relations, and technique cues. Use it to settle
the open model questions empirically (qwen2.5-coder:7b vs base qwen2.5:7b vs
gpt-oss:20b) instead of guessing.

Gold-set format (JSON list), see config/eval_gold.example.json:

    [
      {
        "text": "<transcript chunk text>",
        "entities": [{"name": "lateral raise", "type": "Method"}],
        "relations": [{"subject": "...", "predicate": "...", "object": "..."}],
        "cues": [{"exercise": "lateral raise", "cue": "don't shrug at the top",
                  "kind": "mistake"}]
      }
    ]

Matching is case-insensitive and, for entities/cues, fuzzy on the text field
(token-set overlap >= --fuzzy, default 0.6) so minor wording differences in the
cue/predicate don't count as misses. Run:

    python -m yt_kg.eval_extract --gold config/eval_gold.json
    python -m yt_kg.eval_extract --gold config/eval_gold.json --json > report.json
"""
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from yt_kg.extract import _extract_chunk


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _token_set_ratio(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 1.0 if ta == tb else 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def report(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
        }


def _match_exact(pred: list[tuple], gold: list[tuple], acc: PRF) -> None:
    gold_left = list(gold)
    for p in pred:
        if p in gold_left:
            acc.tp += 1
            gold_left.remove(p)
        else:
            acc.fp += 1
    acc.fn += len(gold_left)


def _match_fuzzy(pred: list[dict], gold: list[dict], key: str, fuzzy: float, acc: PRF) -> None:
    """Match on an exact anchor field plus a fuzzy text field `key`."""
    gold_left = list(gold)
    for p in pred:
        hit = None
        for g in gold_left:
            if p.get("anchor") == g.get("anchor") and _token_set_ratio(p[key], g[key]) >= fuzzy:
                hit = g
                break
        if hit is not None:
            acc.tp += 1
            gold_left.remove(hit)
        else:
            acc.fp += 1
    acc.fn += len(gold_left)


def evaluate(gold_path: Path, fuzzy: float) -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    ent_acc, rel_acc, cue_acc = PRF(), PRF(), PRF()

    for case in gold:
        result = _extract_chunk(case["text"])

        # Entities: exact (name, type)
        pred_e = [(_norm(e.name), e.type) for e in result.entities]
        gold_e = [(_norm(e["name"]), e["type"]) for e in case.get("entities", [])]
        _match_exact(pred_e, gold_e, ent_acc)

        # Relations: exact (subject, predicate, object), all normalized
        pred_r = [(_norm(r.subject), _norm(r.predicate), _norm(r.object)) for r in result.relations]
        gold_r = [(_norm(r["subject"]), _norm(r["predicate"]), _norm(r["object"]))
                  for r in case.get("relations", [])]
        _match_exact(pred_r, gold_r, rel_acc)

        # Cues: anchor on (exercise, kind), fuzzy on cue text
        pred_c = [{"anchor": (_norm(c.exercise), c.kind), "cue": c.cue} for c in result.cues]
        gold_c = [{"anchor": (_norm(c["exercise"]), c.get("kind", "cue")), "cue": c["cue"]}
                  for c in case.get("cues", [])]
        _match_fuzzy(pred_c, gold_c, "cue", fuzzy, cue_acc)

    return {
        "cases": len(gold),
        "entities": ent_acc.report(),
        "relations": rel_acc.report(),
        "cues": cue_acc.report(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate extraction against a gold set")
    ap.add_argument("--gold", type=Path, default=Path("config/eval_gold.json"))
    ap.add_argument("--fuzzy", type=float, default=0.6, help="token-set ratio threshold for cue text")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    if not args.gold.exists():
        raise SystemExit(
            f"Gold set not found: {args.gold}\n"
            f"Copy config/eval_gold.example.json to {args.gold} and label ~30 chunks."
        )

    report = evaluate(args.gold, args.fuzzy)
    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"\nExtraction eval — {report['cases']} cases\n")
    print(f"{'metric':<12}{'P':>8}{'R':>8}{'F1':>8}{'tp':>6}{'fp':>6}{'fn':>6}")
    for section in ("entities", "relations", "cues"):
        s = report[section]
        print(f"{section:<12}{s['precision']:>8}{s['recall']:>8}{s['f1']:>8}"
              f"{s['tp']:>6}{s['fp']:>6}{s['fn']:>6}")
    print()


if __name__ == "__main__":
    main()
