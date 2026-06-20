import sqlite3

conn = sqlite3.connect("data/jobs.sqlite")
conn.row_factory = sqlite3.Row

total   = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
skipped = conn.execute("SELECT COUNT(*) FROM videos WHERE skipped=1").fetchone()[0]
active  = total - skipped

dl  = conn.execute("SELECT COUNT(*) FROM videos WHERE downloaded_at IS NOT NULL").fetchone()[0]
tr  = conn.execute("SELECT COUNT(*) FROM videos WHERE transcribed_at IS NOT NULL").fetchone()[0]
ch  = conn.execute("SELECT COUNT(*) FROM videos WHERE chunked_at IS NOT NULL").fetchone()[0]
ex  = conn.execute("SELECT COUNT(*) FROM videos WHERE extracted_at IS NOT NULL").fetchone()[0]
gr  = conn.execute("SELECT COUNT(*) FROM videos WHERE graphed_at IS NOT NULL").fetchone()[0]
ci  = conn.execute("SELECT COUNT(*) FROM videos WHERE cited_at IS NOT NULL").fetchone()[0]

print(f"Total videos : {total}  (active={active}, skipped={skipped})")
print(f"  downloaded : {dl}/{active}")
print(f"  transcribed: {tr}/{active}")
print(f"  chunked    : {ch}/{active}")
print(f"  extracted  : {ex}/{active}")
print(f"  graphed    : {gr}/{active}")
print(f"  cited      : {ci}/{active}")

errors = conn.execute(
    "SELECT video_id, error_stage, last_error FROM videos WHERE last_error IS NOT NULL"
).fetchall()

if errors:
    print(f"\nErrors ({len(errors)}):")
    for e in errors:
        print(f"  [{e['error_stage']}] {e['video_id']}: {str(e['last_error'])[:120]}")
else:
    print("\nNo errors recorded.")
