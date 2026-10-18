"""Scan a folder (repo, docs, notes) into the KB: one entry per text file.  usage: ingest.py <folder>"""
import pathlib, sys
import app

EXT = {".md", ".txt", ".py", ".sql", ".bicep", ".tf", ".yml", ".yaml", ".json", ".ts", ".js"}
SKIP = {"node_modules", ".git", ".venv", "__pycache__"}
root = pathlib.Path(sys.argv[1]).resolve()
n = 0
for p in root.rglob("*"):
    if p.is_file() and p.suffix in EXT and not SKIP & set(p.parts) and p.stat().st_size < 200_000:
        try:
            app.add(f"{root.name}/{p.relative_to(root)}", p.read_text()[:20_000], [p.suffix[1:], "ingested"], f"file:{p}")
            n += 1
        except (ValueError, UnicodeDecodeError):
            pass
print(f"ingested {n} files")
