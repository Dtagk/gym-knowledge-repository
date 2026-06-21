"""Create GitHub issues for Epic 5 stories."""
import subprocess, json, tempfile, os

REPO = "Dtagk/gym-knowledge-repository"

STORIES = [
    (5, "Story 5.1: Search-Seeded Candidate Staging Tier", """## Story 5.1: Search-Seeded Candidate Staging Tier

As a developer, I want discover.py to read a `searches:` block and record results as staged candidates instead of pipeline-ready videos, so that I can grow the corpus from topics without each video immediately consuming download/transcribe resources.

### Config shape

```yaml
searches:
  - query: "rir vs rpe hypertrophy"
    limit: 30
  - query: "lengthened partials meta-analysis"
    limit: 30
```

### Acceptance Criteria

- Given a `searches:` block, when discover.py runs, up to `limit` rows inserted with `source='search'`, `status='candidate'`, query recorded, no stage timestamps set
- Given a candidate already exists, when discovery re-runs, no duplicate row (INSERT OR IGNORE on video_id)
- Given candidates with `status='candidate'`, when filter/download stages run, only `status='approved'` rows are selected
- Existing config entries behave unchanged (`source='config'`, `status='approved'`)

**Note:** Schema columns (status, source, query, relevance_score) and `_discover_searches()` already scaffolded in `fix/review-patches-r2-epic5-scaffold`.

**Part of:** Epic 5 — Semi-Automated Video Discovery"""),

    (5, "Story 5.2: Embedding Relevance Scoring + Auto-Promotion", """## Story 5.2: Embedding Relevance Scoring + Auto-Promotion

As a developer, I want candidates scored against anchor topics and auto-sorted by confidence, so that obvious matches enter the pipeline automatically and only borderline videos need my attention.

### Config shape

```yaml
discovery:
  anchors:
    - "evidence-based hypertrophy training volume and intensity"
    - "biomechanics of compound lifts and injury prevention"
  promote_threshold: 0.55
  reject_threshold: 0.30
  use_approved_centroid: true
```

### Acceptance Criteria

- Given a candidate with title + description, when scoring runs, `relevance_score` (cosine vs anchor vectors) is persisted; computed from metadata only, no audio download (NFR8)
- Given a candidate scoring >= `promote_threshold`, its `status` becomes `approved` and flows into `filter` on next run
- Given a candidate scoring < `reject_threshold`, its `status` becomes `rejected` and is excluded from all downstream stages
- Given a candidate in the middle band, `status` remains `candidate` and appears in `scripts/_status.py` or `_review.py`
- Given `use_approved_centroid: true` and at least one approved video with embeddings, anchor set includes centroid of approved-video chunk embeddings

**Part of:** Epic 5 — Semi-Automated Video Discovery"""),

    (5, "Story 5.3: Trusted-Channel New-Upload Polling", """## Story 5.3: Trusted-Channel New-Upload Polling

As a developer, I want trusted channels to surface only their new uploads as scored candidates, so that I keep up with creators I trust without re-listing or re-handpicking.

### Acceptance Criteria

- Given a `type: channel` entry with `max_age_days`, when discovery runs, uploads newer than `max_age_days` are recorded as candidates with `source='channel_new'` and pass through the scoring/promotion gate
- Given a trusted channel with `auto_approve: true`, when its new uploads are discovered, they are recorded directly as `approved`, still subject to existing `filter` rules
- Given a channel's uploads were already discovered on a prior run, when discovery runs again, only genuinely new uploads are added (no duplicates)

**Part of:** Epic 5 — Semi-Automated Video Discovery"""),
]

for milestone, title, body in STORIES:
    payload = json.dumps({"title": title, "body": body, "milestone": milestone})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(payload)
        tmp = f.name
    result = subprocess.run(
        ["gh", "api", f"repos/{REPO}/issues", "--method", "POST", "--input", tmp],
        capture_output=True, text=True,
    )
    os.unlink(tmp)
    if result.returncode == 0:
        url = json.loads(result.stdout).get("html_url", "?")
        print(f"Created: {url}")
    else:
        print(f"FAILED: {title}")
        print(result.stderr[:300])
