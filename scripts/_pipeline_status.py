from yt_kg.db import init_db

conn = init_db()
approved = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved'").fetchone()[0]
downloaded = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND downloaded_at IS NOT NULL").fetchone()[0]
transcribed = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND transcribed_at IS NOT NULL").fetchone()[0]
extracted = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND extracted_at IS NOT NULL").fetchone()[0]
embedded = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND chunked_at IS NOT NULL").fetchone()[0]
graphed = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND graphed_at IS NOT NULL").fetchone()[0]
try:
    classified = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND body_parts IS NOT NULL").fetchone()[0]
except Exception:
    classified = 0
errors = conn.execute("SELECT COUNT(*) FROM videos WHERE status='approved' AND last_error IS NOT NULL").fetchone()[0]

print(f"Approved:    {approved}")
print(f"Downloaded:  {downloaded}")
print(f"Transcribed: {transcribed}")
print(f"Extracted:   {extracted}")
print(f"Embedded:    {embedded}")
print(f"Graphed:     {graphed}")
print(f"Classified:  {classified}")
print(f"With errors: {errors}")

# Show what needs downloading next
pending_dl = conn.execute(
    "SELECT video_id, title FROM videos WHERE status='approved' AND downloaded_at IS NULL AND skipped=0 LIMIT 5"
).fetchall()
if pending_dl:
    print(f"\nNext to download ({len(pending_dl)} shown):")
    for r in pending_dl:
        print(f"  {r[0]}: {(r[1] or '')[:60]}")
