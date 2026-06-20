import sqlite3
conn = sqlite3.connect("data/jobs.sqlite")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT video_id, extracted_at, graphed_at, cited_at, error_stage, last_error FROM videos ORDER BY video_id"
).fetchall()
print(f"{'video_id':<20} {'ext':3} {'grp':3} {'cit':3}  error")
for r in rows:
    ext = "Y" if r["extracted_at"] else " "
    grp = "Y" if r["graphed_at"] else " "
    cit = "Y" if r["cited_at"] else " "
    err = f"[{r['error_stage']}] {(r['last_error'] or '')[:60]}" if r["error_stage"] else ""
    print(f"{r['video_id']:<20} {ext:3} {grp:3} {cit:3}  {err}")
