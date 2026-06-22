"""Synthesize cross-video answers by calling the RAG server for curated questions."""
import html
import json
import os
from pathlib import Path

import yaml

_CONFIG = Path(__file__).parent.parent / "config" / "synthesis_questions.yaml"
_OUTPUT = Path("docs/data/synthesis.json")
_HTML_OUTPUT = Path("docs/synthesis.html")
_RAG_URL = os.environ.get("RAG_URL", "http://localhost:8000")


def synthesize() -> None:
    import httpx

    with open(_CONFIG, encoding="utf-8") as f:
        questions = yaml.safe_load(f)["questions"]

    results = []
    for q in questions:
        try:
            resp = httpx.post(
                f"{_RAG_URL}/ask",
                json={"query": q, "limit": 5},
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
            sources = [
                {"title": s["title"], "url": s["url"]}
                for s in data.get("sources", [])[:3]
            ]
            results.append({"question": q, "answer": data["answer"], "sources": sources})
            print(f"OK: {q[:60]}")
        except Exception as e:
            print(f"SKIP ({e}): {q[:60]}")

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(results)} synthesis cards to {_OUTPUT}")

    _render_html(results)
    print(f"Rendered {_HTML_OUTPUT}")


def _render_html(results: list[dict]) -> None:
    """Render synthesis cards into a static, self-contained browsable page."""
    from datetime import datetime, timezone

    cards = []
    for r in results:
        q = html.escape(r["question"])
        # answer is model-generated prose; escape then restore paragraph breaks
        ans = html.escape(r["answer"]).replace("\n\n", "</p><p>").replace("\n", "<br>")
        sources = "".join(
            f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">'
            f'{html.escape(s["title"])}</a></li>'
            for s in r.get("sources", [])
        )
        cards.append(
            f'<article class="card"><h2>{q}</h2><div class="answer"><p>{ans}</p></div>'
            f'<details class="sources"><summary>{len(r.get("sources", []))} sources</summary>'
            f"<ul>{sources}</ul></details></article>"
        )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Corpus Synthesis — Gym Knowledge Base</title>
<style>
  :root {{ --bg:#0f1115; --card:#181b22; --fg:#e6e8eb; --muted:#9aa0a8; --accent:#6ea8fe; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.6 system-ui,sans-serif; background:var(--bg); color:var(--fg); }}
  header {{ padding:2rem 1.5rem 1rem; max-width:820px; margin:0 auto; }}
  header h1 {{ margin:0 0 .25rem; font-size:1.6rem; }}
  header p {{ margin:0; color:var(--muted); font-size:.9rem; }}
  header a {{ color:var(--accent); }}
  main {{ max-width:820px; margin:0 auto; padding:0 1.5rem 4rem; }}
  .card {{ background:var(--card); border:1px solid #232733; border-radius:12px;
           padding:1.25rem 1.5rem; margin:1rem 0; }}
  .card h2 {{ margin:0 0 .75rem; font-size:1.1rem; color:var(--accent); }}
  .answer p {{ margin:0 0 .75rem; }}
  .sources {{ margin-top:.75rem; font-size:.9rem; }}
  .sources summary {{ cursor:pointer; color:var(--muted); }}
  .sources a {{ color:var(--accent); text-decoration:none; }}
  .sources a:hover {{ text-decoration:underline; }}
</style></head>
<body>
<header>
  <h1>Corpus Synthesis</h1>
  <p>Cross-video answers generated from the knowledge base · {generated} ·
     <a href="index.html">&larr; back to browser</a></p>
</header>
<main>{"".join(cards) if cards else "<p>No synthesis cards yet. Run the synthesize stage with the RAG server up.</p>"}</main>
</body></html>"""

    _HTML_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _HTML_OUTPUT.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    synthesize()
