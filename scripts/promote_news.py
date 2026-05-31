from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
draft = ROOT / "public" / "import" / "pest-megye-news-draft.json"
live = ROOT / "public" / "import" / "pest-megye-news.json"
if not draft.exists():
    raise SystemExit("Nincs draft fájl: public/import/pest-megye-news-draft.json")
live.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
print(f"Élesítve: {live}")
