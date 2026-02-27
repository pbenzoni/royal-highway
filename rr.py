import html
import json
import random
import re
import time
from typing import List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


ROYALROAD_BASE = "https://www.royalroad.com"


def parse_fiction_slug(fiction_url: str) -> str:
    """
    Extracts "41656/chaotic-craftsman-worships-the-cube" from:
    https://www.royalroad.com/fiction/41656/chaotic-craftsman-worships-the-cube
    """
    p = urlparse(fiction_url)
    if not p.netloc:
        raise ValueError("That does not look like a valid URL.")

    path = (p.path or "").strip("/")
    parts = path.split("/")

    # Expected: ["fiction", "<id>", "<slug>"]
    if len(parts) < 3 or parts[0] != "fiction":
        raise ValueError('Expected a Royal Road fiction URL like "https://www.royalroad.com/fiction/<id>/<slug>".')

    fiction_id = parts[1]
    fiction_slug = parts[2]
    if not fiction_id.isdigit():
        raise ValueError("Fiction ID did not look numeric.")

    return f"{fiction_id}/{fiction_slug}"


def fetch_fiction_page(session: requests.Session, fiction_url: str) -> str:
    r = session.get(fiction_url, timeout=30)
    r.raise_for_status()
    return r.text


def _extract_js_array(text: str, var_name: str) -> str:
    """
    Extract a JSON array assigned to something like:
      window.chapters = [ ... ];
    using bracket matching.
    """
    idx = text.find(var_name)
    if idx == -1:
        raise ValueError(f'Could not find "{var_name}" in the HTML.')

    bracket_start = text.find("[", idx)
    if bracket_start == -1:
        raise ValueError(f'Could not locate "[" after "{var_name}".')

    depth = 0
    for i in range(bracket_start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[bracket_start : i + 1]

    raise ValueError(f'Could not bracket-match JSON array for "{var_name}".')


def extract_window_chapters(html_doc: str) -> List[dict]:
    """
    Finds the <script> block containing window.chapters, then parses it as JSON.
    """
    soup = BeautifulSoup(html_doc, "html.parser")
    scripts = soup.find_all("script")
    target = None
    for s in scripts:
        if not s.string:
            continue
        if "window.chapters" in s.string:
            target = s.string
            break

    if not target:
        target = html_doc
        if "window.chapters" not in target:
            raise ValueError("Could not find window.chapters in the page.")

    arr_text = _extract_js_array(target, "window.chapters")
    chapters = json.loads(arr_text)

    if not isinstance(chapters, list):
        raise ValueError("window.chapters did not parse into a list.")

    for ch in chapters:
        if "id" not in ch:
            raise ValueError("A chapter entry is missing an id.")

    return chapters


def chunk_chapters(chapters: List[dict], size: int = 10) -> List[List[dict]]:
    return [chapters[i : i + size] for i in range(0, len(chapters), size)]


def _polite_sleep(min_delay: float, max_delay: float) -> None:
    if min_delay < 0:
        min_delay = 0
    if max_delay < min_delay:
        max_delay = min_delay
    time.sleep(random.uniform(min_delay, max_delay))


# ---------- Bracket-to-bold (safe) ----------

def bracket_to_bold_html_from_text(text: str) -> str:
    """
    Converts bracketed segments into bold text and removes the brackets:

    - <something>  -> <strong>something</strong>
    - [something]  -> <strong>something</strong>
    - {something}  -> <strong>something</strong>

    Everything else is HTML-escaped to avoid injection.
    """
    replacements: List[str] = []

    def mark(content: str) -> str:
        idx = len(replacements)
        token = f"@@BOLD{idx}@@"
        replacements.append(f"<strong>{html.escape(content)}</strong>")
        return token

    patterns = [
        r"<([^<>]+)>",
        r"\[([^\[\]]+)\]",
        r"\{([^{}]+)\}",
    ]
    for pat in patterns:
        text = re.sub(pat, lambda m: mark(m.group(1)), text)

    text = text.translate(str.maketrans({"<": "", ">": "", "[": "", "]": "", "{": "", "}": ""}))

    escaped = html.escape(text)
    for i, rep in enumerate(replacements):
        escaped = escaped.replace(f"@@BOLD{i}@@", rep)

    return escaped


def _split_bracket_spans(s: str) -> List[Tuple[str, str]]:
    """
    Returns a list of (kind, value) segments where kind is "text" or "bold".
    This is used to apply bracket-to-bold inside HTML text nodes without treating
    real HTML tags as bracket syntax.
    """
    # Order matters: angle, square, curly.
    pattern = re.compile(r"(<[^<>]+>|\[[^\[\]]+\]|\{[^{}]+\})")
    parts: List[Tuple[str, str]] = []
    last = 0
    for m in pattern.finditer(s):
        if m.start() > last:
            parts.append(("text", s[last:m.start()]))
        token = m.group(1)
        if token.startswith("<") and token.endswith(">"):
            parts.append(("bold", token[1:-1]))
        elif token.startswith("[") and token.endswith("]"):
            parts.append(("bold", token[1:-1]))
        elif token.startswith("{") and token.endswith("}"):
            parts.append(("bold", token[1:-1]))
        last = m.end()
    if last < len(s):
        parts.append(("text", s[last:]))
    return parts


def apply_bracket_bolding_to_text_nodes(fragment_soup: BeautifulSoup) -> None:
    """
    Walks text nodes in a soup fragment, and replaces bracketed spans with <strong>.
    This preserves existing markup like <em>, <p>, <hr>, etc.
    """
    for node in list(fragment_soup.descendants):
        if isinstance(node, NavigableString):
            parent = node.parent
            if not isinstance(parent, Tag):
                continue
            original = str(node)
            if not any(ch in original for ch in ("<", ">", "[", "]", "{", "}")):
                continue

            segments = _split_bracket_spans(original)
            if len(segments) == 1 and segments[0][0] == "text":
                # Still strip stray brackets if present
                cleaned = segments[0][1].translate(str.maketrans({"<": "", ">": "", "[": "", "]": "", "{": "", "}": ""}))
                if cleaned != original:
                    node.replace_with(cleaned)
                continue

            new_nodes = []
            for kind, val in segments:
                if kind == "text":
                    new_nodes.append(val.translate(str.maketrans({"<": "", ">": "", "[": "", "]": "", "{": "", "}": ""})))
                else:
                    strong = fragment_soup.new_tag("strong")
                    strong.string = val
                    new_nodes.append(strong)

            # Replace this text node with the sequence
            if new_nodes:
                # Insert before, then remove original
                for new in new_nodes:
                    node.insert_before(new)
                node.extract()


def strip_single_line_between_brs(
    fragment_soup: BeautifulSoup,
    max_chars: int = 200,
) -> None:
    """Remove short single-line text that sits *between* two consecutive <br> tags.

    This targets common "one-liner" noise like "Patreon" adverts or short author notes
    inserted between line breaks.

    Pattern removed (whitespace ignored):
      <br>  TEXT  <br>

    Only removes when:
    - There are no other tags in between (just a single text node after trimming)
    - The trimmed text is non-empty and <= max_chars
    """

    def _is_br(tag: Tag) -> bool:
        return isinstance(tag, Tag) and tag.name == "br"

    changed = True
    while changed:
        changed = False
        # Iterate over a snapshot because we'll be mutating the tree.
        for br in list(fragment_soup.find_all("br")):
            nxt = br.next_sibling

            # Skip pure whitespace siblings.
            while isinstance(nxt, NavigableString) and not str(nxt).strip():
                nxt = nxt.next_sibling

            if not isinstance(nxt, NavigableString):
                continue

            text = str(nxt).strip()
            if not text or len(text) > max_chars:
                continue

            nxt2 = nxt.next_sibling
            while isinstance(nxt2, NavigableString) and not str(nxt2).strip():
                nxt2 = nxt2.next_sibling

            if not _is_br(nxt2):
                continue

            # Remove: <br> [whitespace] TEXT [whitespace] <br>
            nxt.extract()

            # Also remove any whitespace-only text nodes now sitting between the brs.
            probe = br.next_sibling
            while isinstance(probe, NavigableString) and not str(probe).strip():
                tmp = probe
                probe = probe.next_sibling
                tmp.extract()

            changed = True
            break


# ---------- HTML-preserving extraction (sanitized) ----------

_ALLOWED_TAGS = {
    "p", "br", "em", "i", "strong", "b",
    "hr", "blockquote",
    "ul", "ol", "li",
    "h2", "h3", "h4",
    "pre", "code",
    "span",
}

_ALLOWED_ATTRS = {
    # Keep very limited attributes, mostly for basic formatting
    "span": {"class"},
    "p": {"class"},
    "blockquote": {"class"},
    "hr": {"class"},
    "pre": {"class"},
    "code": {"class"},
    "em": set(),
    "i": set(),
    "strong": set(),
    "b": set(),
    "br": set(),
    "ul": {"class"},
    "ol": {"class"},
    "li": {"class"},
    "h2": {"class"},
    "h3": {"class"},
    "h4": {"class"},
}


def sanitize_fragment_html(fragment_html: str) -> str:
    """
    Sanitizes an HTML fragment:
    - Removes disallowed tags by unwrapping them
    - Removes disallowed attributes
    - Drops script/style outright
    """
    frag = BeautifulSoup(fragment_html, "html.parser")

    for bad in frag.find_all(["script", "style"]):
        bad.decompose()

    for tag in list(frag.find_all(True)):
        if tag.name not in _ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = _ALLOWED_ATTRS.get(tag.name, set())
        # Remove all attrs not in allowed
        for attr in list(tag.attrs.keys()):
            if attr not in allowed:
                del tag.attrs[attr]

    apply_bracket_bolding_to_text_nodes(frag)
    strip_single_line_between_brs(frag, max_chars=200)
    return frag.decode_contents()


def fetch_chapter_content(
    session: requests.Session,
    chapter_path: str,
    min_delay: float = 2.0,
    max_delay: float = 4.0,
    retries: int = 2,
) -> Tuple[str, str, str, str]:
    """
    Fetches a chapter page and extracts:
      - Title
      - Raw text (plain)
      - Text HTML (escaped, with bracket spans bolded)
      - Content HTML (sanitized fragment, preserving formatting)

    Uses polite pacing and basic backoff on 429 or 503.
    """
    url = chapter_path
    if chapter_path.startswith("/"):
        url = ROYALROAD_BASE + chapter_path

    last_err = None
    for _attempt in range(retries + 1):
        _polite_sleep(min_delay, max_delay)

        r = session.get(url, timeout=30)
        if r.status_code in (429, 503):
            _polite_sleep(min_delay * 2, max_delay * 2)
            last_err = RuntimeError(f"Got HTTP {r.status_code}")
            continue

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        h1 = soup.select_one("h1")
        if h1 and h1.get_text(strip=True):
            page_title = h1.get_text(strip=True)
        else:
            t = soup.select_one("title")
            page_title = t.get_text(strip=True) if t else "Untitled"

        content = soup.select_one(".chapter-inner.chapter-content")
        if not content:
            content = soup.select_one(".chapter-content")
        if not content:
            raise ValueError("Could not find chapter content container on the page.")

        text_raw = content.get_text("\n", strip=True)
        text_html = bracket_to_bold_html_from_text(text_raw)

        fragment_html = content.decode_contents()
        content_html = sanitize_fragment_html(fragment_html)

        return page_title, text_raw, text_html, content_html

    raise last_err or RuntimeError("Failed to fetch chapter after retries.")
