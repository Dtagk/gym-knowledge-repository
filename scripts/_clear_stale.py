import sqlite3
conn = sqlite3.connect("data/jobs.sqlite")
cur = conn.execute(
    "UPDATE videos SET last_error=NULL, error_stage=NULL "
    "WHERE downloaded_at IS NOT NULL AND error_stage='download'"
)
print(f"Cleared stale errors for {cur.rowcount} videos")
conn.commit()
conn.close()
