import os
import time
from typing import Dict, Any, List

from flask import Flask, render_template, request, redirect, url_for, flash, Response
import requests

from cache import CacheDB
from rr import (
    parse_fiction_slug,
    fetch_fiction_page,
    extract_window_chapters,
    chunk_chapters,
    fetch_chapter_content,
)

APP_SECRET = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")
CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.sqlite")

db = CacheDB(CACHE_DB_PATH)

# In-memory cache is still useful for speed within a single run,
# but disk cache is the source of truth across restarts.
FICTION_MEM: Dict[str, Dict[str, Any]] = {}


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "RoyalRoadChunker/0.1 (+contact: you@example.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def get_fiction_cached(fiction_slug: str) -> Dict[str, Any] | None:
    if fiction_slug in FICTION_MEM:
        return FICTION_MEM[fiction_slug]
    disk = db.get_fiction(fiction_slug)
    if disk:
        FICTION_MEM[fiction_slug] = {
            "fetched_at": disk["fetched_at"],
            "chapters": disk["chapters"],
        }
        return FICTION_MEM[fiction_slug]
    return None


def put_fiction_cached(fiction_slug: str, chapters: List[dict]) -> None:
    db.put_fiction(fiction_slug, chapters)
    FICTION_MEM[fiction_slug] = {"fetched_at": time.time(), "chapters": chapters}


app = Flask(__name__)
app.secret_key = APP_SECRET


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/load")
def load_fiction():
    fiction_url = (request.form.get("fiction_url") or "").strip()
    refresh = (request.form.get("refresh") or "").strip() == "1"

    if not fiction_url:
        flash('Missing "fiction_url".', "danger")
        return redirect(url_for("index"))

    try:
        fiction_slug = parse_fiction_slug(fiction_url)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("index"))

    if not refresh:
        cached = get_fiction_cached(fiction_slug)
        if cached:
            return redirect(url_for("show_chunks", fiction_slug=fiction_slug))

    session = create_session()

    try:
        html_doc = fetch_fiction_page(session, fiction_url)
        chapters = extract_window_chapters(html_doc)
    except Exception as e:
        flash(f"Failed to fetch or parse chapters: {e}", "danger")
        return redirect(url_for("index"))

    put_fiction_cached(fiction_slug, chapters)
    return redirect(url_for("show_chunks", fiction_slug=fiction_slug))


@app.get("/fiction/<path:fiction_slug>")
def show_chunks(fiction_slug: str):
    cached = get_fiction_cached(fiction_slug)
    if not cached:
        flash("No cached chapters found. Paste the fiction URL again.", "warning")
        return redirect(url_for("index"))

    chapters = cached["chapters"]
    chunks = chunk_chapters(chapters, size=10)

    chunk_links = []
    for idx, chunk in enumerate(chunks):
        ids = ",".join(str(ch["id"]) for ch in chunk)
        first_title = chunk[0].get("title") or f"Chapter {chunk[0]['id']}"
        last_title = chunk[-1].get("title") or f"Chapter {chunk[-1]['id']}"
        chunk_links.append(
            {
                "index": idx,
                "count": len(chunk),
                "first_title": first_title,
                "last_title": last_title,
                "href_text": url_for("compile_chunk", fiction_slug=fiction_slug, ids=ids, mode="text"),
                "href_html": url_for("compile_chunk", fiction_slug=fiction_slug, ids=ids, mode="html"),
                "ids": ids,
            }
        )

    return render_template(
        "chunks.html",
        fiction_slug=fiction_slug,
        total=len(chapters),
        chunk_links=chunk_links,
        fetched_at=cached["fetched_at"],
    )


@app.get("/fiction/<path:fiction_slug>/compile")
def compile_chunk(fiction_slug: str):
    ids_raw = (request.args.get("ids") or "").strip()
    mode = (request.args.get("mode") or "text").strip().lower()
    download = (request.args.get("download") or "").strip() == "1"

    if mode not in ("text", "html"):
        mode = "text"

    if not ids_raw:
        flash('Missing "ids" query parameter.', "danger")
        return redirect(url_for("show_chunks", fiction_slug=fiction_slug))

    try:
        chapter_ids = [int(x) for x in ids_raw.split(",") if x.strip()]
    except ValueError:
        flash('Bad "ids" format. Expected comma-separated integers.', "danger")
        return redirect(url_for("show_chunks", fiction_slug=fiction_slug))

    cached = get_fiction_cached(fiction_slug)
    if not cached:
        flash("No cached chapters found. Paste the fiction URL again.", "warning")
        return redirect(url_for("index"))

    session = create_session()

    try:
        min_delay = float(request.args.get("min_delay", "2.0"))
        max_delay = float(request.args.get("max_delay", "4.0"))
    except ValueError:
        min_delay, max_delay = 2.0, 4.0

    chapters_by_id = {int(ch["id"]): ch for ch in cached["chapters"]}

    compiled_parts: List[Dict[str, str]] = []
    for cid in chapter_ids:
        disk_hit = db.get_chapter(fiction_slug, cid)
        if disk_hit:
            compiled_parts.append(
                {
                    "title": disk_hit["title"],
                    "text_raw": disk_hit["text_raw"],
                    "text_html": disk_hit["text_html"],
                    "content_html": disk_hit["content_html"],
                }
            )
            continue

        if cid in chapters_by_id and chapters_by_id[cid].get("url"):
            chapter_path = chapters_by_id[cid]["url"]
        else:
            chapter_path = f"/fiction/{fiction_slug}/chapter/{cid}"

        try:
            title, text_raw, text_html, content_html = fetch_chapter_content(
                session=session,
                chapter_path=chapter_path,
                min_delay=min_delay,
                max_delay=max_delay,
            )
        except Exception as e:
            title = f"Chapter {cid}"
            text_raw = f"[Failed to fetch chapter {cid}: {e}]"
            text_html = text_raw
            content_html = text_raw

        db.put_chapter(
            fiction_slug=fiction_slug,
            chapter_id=cid,
            title=title,
            text_raw=text_raw,
            text_html=text_html,
            content_html=content_html,
        )

        compiled_parts.append(
            {"title": title, "text_raw": text_raw, "text_html": text_html, "content_html": content_html}
        )

    if download:
        out_lines: List[str] = []
        for part in compiled_parts:
            out_lines.append(part["title"])
            out_lines.append("=" * len(part["title"]))
            out_lines.append(part["text_raw"])
            out_lines.append("")
        body = "\n".join(out_lines)
        filename = fiction_slug.replace("/", "_") + "_chunk.txt"
        return Response(
            body,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return render_template(
        "compiled.html",
        fiction_slug=fiction_slug,
        ids_raw=ids_raw,
        mode=mode,
        parts=compiled_parts,
        min_delay=min_delay,
        max_delay=max_delay,
        download_href=url_for("compile_chunk", fiction_slug=fiction_slug, ids=ids_raw, mode=mode, download=1),
    )


if __name__ == "__main__":
    app.run(debug=True)
