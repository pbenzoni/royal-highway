# Royal Road Chunker (Flask + Bootstrap)

## What it does
- Paste a Royal Road fiction URL
- Parses `window.chapters` from the page HTML
- Chunks chapters into groups of ten
- Click a chunk to fetch each chapter page and compile text (with polite pacing)
- Disk-backed caching (SQLite) so restarts keep chapters and fetched chapter-content
- Two compile modes:
  - Text mode: Plain-text extraction, with `<...>`, `[...]`, and `{...}` turned into **bold** text (brackets removed)
  - HTML mode: Preserves basic formatting (italics, breaks, lists, dividers) using a sanitized HTML fragment, and also bolds bracketed spans inside text

## Run locally
- Create and activate a virtual environment:
  - `python -m venv .venv`
  - Windows: `.venv\Scripts\activate`
  - macOS/Linux: `source .venv/bin/activate`

- Install dependencies:
  - `pip install -r requirements.txt`

- Run:
  - `flask --app app run --debug`
  - Or: `python app.py`

- Visit:
  - `http://127.0.0.1:5000`

## Cache configuration
- By default the app writes `cache.sqlite` in the project directory.
- You can override via environment variable:
  - `CACHE_DB_PATH=somewhere/cache.sqlite`

## Notes
- The HTML mode sanitizer keeps a limited set of tags and strips most attributes to reduce injection risk.
