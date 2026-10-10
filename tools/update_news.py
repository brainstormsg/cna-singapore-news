#!/usr/bin/env python3
"""Build a rolling seven-calendar-day CNA Singapore and World audio-news archive.

Candidates come from CNA's public search index, supplemented by its live news
sitemap. Only /singapore/ and /world/ articles with a BeyondWords player and a
direct CDN MP3 are published. Source audio is never downloaded.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "articles.json"
OUT = ROOT / "index.html"
LOG = ROOT / "data" / "update.log"
SITEMAP = "https://www.channelnewsasia.com/api/v1/sitemap-news-feed?_format=xml"
ALGOLIA_ENDPOINT = "https://KKWFBQ38XF-dsn.algolia.net/1/indexes/cnarevamp-ezrqv5hx/query"
ALGOLIA_APP_ID = "KKWFBQ38XF"
ALGOLIA_API_KEY = "e4b61225b5a00162761c501328a58ac7"
BW_API = "https://api.beyondwords.io/v1/projects/{project}/player/by_content_id/{podcast}"
CNA_HOST = "www.channelnewsasia.com"
SECTIONS = ("singapore", "world")
KEEP_DAYS = 7
SGT = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def now() -> datetime:
    return datetime.now(SGT)


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{now():%Y-%m-%d %H:%M:%S %z} {message}"
    print(line)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def fetch(url: str, retries: int = 3) -> str:
    """Fetch text with the system curl and a browser-like request profile."""
    command = [
        "/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
        "--compressed", "--retry", str(retries), "--retry-all-errors",
        "--connect-timeout", "10", "--max-time", "35", "-A", UA,
        "-H", "Accept-Language: en-SG,en;q=0.9", url,
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"curl failed ({result.returncode}): {detail}")
    return result.stdout.decode("utf-8", "replace")


def canonical_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != CNA_HOST:
        return None
    if not any(parsed.path.startswith(f"/{section}/") for section in SECTIONS):
        return None
    return urlunsplit(("https", CNA_HOST, parsed.path.rstrip("/"), "", ""))


def archive_cutoff() -> datetime:
    """Start of the local calendar day six days ago: today plus prior six days."""
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=KEEP_DAYS - 1)


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def in_window(value: str | None) -> bool:
    parsed = parse_date(value)
    return bool(parsed and archive_cutoff() <= parsed.astimezone(SGT) <= now())


def algolia_items() -> list[dict]:
    """Return all current-window Singapore and World article aliases from CNA search."""
    cutoff = int(archive_cutoff().timestamp())
    found: dict[str, dict] = {}
    for section in SECTIONS:
        page = 0
        pages = 1
        while page < pages:
            params = (f"query=&hitsPerPage=100&page={page}&filters="
                      f"categories:{section.title()} AND field_release_date >= {cutoff}")
            command = [
                "/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
                "--compressed", "--retry", "2", "--retry-all-errors", "--connect-timeout", "10",
                "--max-time", "35", "-A", UA, "-H", f"X-Algolia-Application-Id: {ALGOLIA_APP_ID}",
                "-H", f"X-Algolia-API-Key: {ALGOLIA_API_KEY}", "-H", "Content-Type: application/json",
                "--data-binary", json.dumps({"params": params}), ALGOLIA_ENDPOINT,
            ]
            result = subprocess.run(command, capture_output=True)
            if result.returncode:
                detail = result.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError(f"Algolia {section} page {page} failed ({result.returncode}): {detail}")
            payload = json.loads(result.stdout.decode("utf-8", "replace"))
            pages = int(payload.get("nbPages", 0))
            for hit in payload.get("hits", []):
                if not isinstance(hit, dict):
                    continue
                alias = str(hit.get("url_alias", ""))
                canonical = canonical_url(f"https://{CNA_HOST}{alias}")
                released = hit.get("field_release_date")
                if not canonical or not isinstance(released, (int, float)):
                    continue
                published = datetime.fromtimestamp(released, tz=timezone.utc).isoformat()
                if in_window(published):
                    found[canonical] = {"url": canonical, "published": published,
                                        "title": html.unescape(str(hit.get("title", "")).strip())}
            page += 1
    return list(found.values())


def sitemap_items(xml: str) -> list[dict]:
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9", "news": "http://www.google.com/schemas/sitemap-news/0.9"}
    root = ET.fromstring(xml)
    found: dict[str, dict] = {}
    for node in root.findall("sm:url", ns):
        canonical = canonical_url(node.findtext("sm:loc", default="", namespaces=ns).strip())
        published = node.findtext("news:news/news:publication_date", default="", namespaces=ns).strip()
        title = html.unescape(node.findtext("news:news/news:title", default="", namespaces=ns)).strip()
        if canonical and published and title and in_window(published):
            found[canonical] = {"url": canonical, "published": published, "title": title}
    return list(found.values())


def meta_value(page: str, key: str, attribute: str = "property") -> str:
    # Supports attributes in either order and either quote style.
    pattern = rf"<meta\b(?=[^>]*\b{attribute}=[\"']{re.escape(key)}[\"'])[^>]*\bcontent=[\"']([^\"']*)[\"'][^>]*>"
    match = re.search(pattern, page, re.I)
    if not match:
        pattern = rf"<meta\b(?=[^>]*\bcontent=[\"']([^\"']*)[\"'])[^>]*\b{attribute}=[\"']{re.escape(key)}[\"'][^>]*>"
        match = re.search(pattern, page, re.I)
    return html.unescape(match.group(1)).strip() if match else ""


def json_ld_metadata(page: str) -> dict[str, str]:
    for raw in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", page, re.I | re.S):
        try:
            payload = json.loads(html.unescape(raw).strip())
        except json.JSONDecodeError:
            continue
        nodes = payload.get("@graph", []) if isinstance(payload, dict) else payload
        if isinstance(nodes, dict):
            nodes = [nodes]
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if any(kind in {"NewsArticle", "Article"} for kind in kinds):
                author = node.get("author", "")
                if isinstance(author, dict):
                    author = author.get("name", "")
                return {
                    "title": str(node.get("headline", "")),
                    "description": str(node.get("description", "")),
                    "published": str(node.get("datePublished", "")),
                    "author": str(author),
                }
    return {}


def first_match(page: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, page, re.I | re.S)
        if match:
            return match.group(1).replace("\\\"", "\"").strip()
    return ""


def article_metadata(page: str, fallback: dict) -> dict:
    ld = json_ld_metadata(page)
    podcast = first_match(page, [r"data-podcast-id=[\"']([^\"']+)[\"']"])
    project = first_match(page, [
        r"mc_texttospeech[\"']?\s*:\s*\{.*?[\"']projectId[\"']\s*:\s*[\"']?(\d+)",
        r"mc_texttospeech.*?projectId\\?[\"']?\s*:\s*\\?[\"']?(\d+)",
    ])
    title = ld.get("title") or meta_value(page, "og:title") or fallback["title"]
    description = ld.get("description") or meta_value(page, "og:description")
    published = ld.get("published") or meta_value(page, "article:published_time") or fallback["published"]
    author = ld.get("author") or meta_value(page, "author", "name")
    image = meta_value(page, "og:image")
    return {"title": html.unescape(title).strip(), "description": html.unescape(description).strip(),
            "published": published, "author": html.unescape(author).strip(), "image": image,
            "podcast_id": podcast, "project_id": project}


def resolve_audio(project: str, podcast: str) -> tuple[str, int | None]:
    payload = json.loads(fetch(BW_API.format(project=project, podcast=podcast)))
    for content in payload.get("content", []):
        for audio in content.get("audio", []):
            url = audio.get("url", "")
            if audio.get("content_type") == "audio/mpeg" and url.startswith("https://cdn.beyondwords.io/") and ".mp3" in url:
                duration = audio.get("duration")
                return url, int(duration) if isinstance(duration, (int, float)) else None
    return "", None


def load_archive() -> list[dict]:
    if not DATA.exists():
        return []
    try:
        content = json.loads(DATA.read_text(encoding="utf-8"))
        return content if isinstance(content, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        log(f"archive read failed: {exc}")
        return []


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


def format_duration(ms: int | None) -> str:
    if not ms:
        return ""
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def render(articles: list[dict], status: str = "") -> str:
    grouped: dict[str, list[tuple[dict, datetime | None]]] = {}
    for article in articles:
        stamp = parse_date(article.get("published"))
        local = stamp.astimezone(SGT) if stamp else None
        label = local.strftime("%A, %-d %B %Y") if local else "Date unknown"
        grouped.setdefault(label, []).append((article, local))
    sections = []
    for label, rows in grouped.items():
        cards = []
        for article, local in rows:
            title = html.escape(article["title"])
            when = local.strftime("%-d %b %Y, %-I:%M %p") if local else "Unknown"
            cards.append(f'''<article class="card" data-src="{html.escape(article['audio'])}" data-title="{title}" data-date="{html.escape(when)}">
  <h3>{title}</h3>
  <p class="meta"><time datetime="{html.escape(article['published'])}">{when}</time>{(' · ' + html.escape(article['author'])) if article.get('author') else ''}</p>
  <div class="player"><button class="playbtn" type="button">▶ Play</button><span class="dur">{format_duration(article.get('duration_ms'))}</span></div>
  <a class="link" href="{html.escape(article['url'])}" target="_blank" rel="noopener">CNA ↗</a>
</article>''')
        sections.append(f"<section><h2>{html.escape(label)}</h2>{''.join(cards)}</section>")
    updated = now().strftime("%-d %b %Y, %-I:%M %p")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive"><title>CNA Singapore &amp; World News</title>
<style>
:root{{--bg:#f6f6f6;--card:#fff;--ink:#202124;--muted:#65676b;--accent:#e51b23;--accent-dark:#bd1118;--line:#dedede}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}header,main{{max-width:860px;margin:auto}}header{{padding:28px 20px 12px;border-top:7px solid var(--accent)}}h1{{margin:0;font-size:30px;line-height:36px;letter-spacing:-.025em}}.sub,.meta{{color:var(--muted);font-size:14px;line-height:20px}}.sub{{margin:4px 0 0}}main{{padding:0 20px 56px}}h2{{font-size:18px;line-height:28px;margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent)}}.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px 20px;margin:0 0 12px}}.card h3{{margin:0 0 4px;font-size:20px;line-height:28px;letter-spacing:-.015em}}.meta{{margin:0 0 12px}}.player{{display:flex;align-items:center;gap:8px}}.playbtn{{font:600 14px/20px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:44px;padding:10px 16px;border-radius:999px;border:1px solid var(--accent);background:#fff;color:var(--accent);cursor:pointer}}.playbtn:hover{{background:#fff1f1}}.playbtn:focus-visible,.bar button:focus-visible,.link:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}.card.playing{{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}}.card.playing .playbtn,.playall{{background:var(--accent);color:#fff}}.playall{{margin-top:12px}}body.has-bar main{{padding-bottom:176px}}.bar{{position:fixed;inset:auto 0 0;background:#202124;color:#fff;padding:10px 12px calc(10px + env(safe-area-inset-bottom));box-shadow:0 -2px 12px #0003;z-index:10}}.bar[hidden]{{display:none}}.bar-inner{{max-width:860px;margin:auto}}.bar-title{{font-size:14px;line-height:20px;font-weight:600;margin-bottom:6px}}.bar-pos,.bar .time{{font-size:12px;color:#c8c8c8}}.bar-controls,.bar-seek{{display:flex;align-items:center;gap:8px}}.bar-seek{{margin-top:4px}}.bar button{{font:inherit;font-size:18px;width:44px;height:44px;border-radius:999px;border:0;background:#414247;color:#fff;cursor:pointer}}.bar button#toggle{{background:var(--accent);width:48px;height:48px}}.bar input[type=range]{{flex:1;accent-color:var(--accent)}}.bar .time{{min-width:34px;text-align:center;font-variant-numeric:tabular-nums}}.dur{{color:var(--muted);font-size:12px}}.link{{display:inline-block;margin-top:10px;color:var(--accent-dark);font-size:14px;font-weight:700;text-decoration:none}}.link:hover{{text-decoration:underline}}.status{{background:#fff3cd;border:1px solid #d9aa35;padding:10px 12px;border-radius:6px;font-size:14px}}@media(max-width:639px){{header,main{{padding-left:16px;padding-right:16px}}h1{{font-size:25px}}.card{{padding:16px}}}}
</style></head><body><header><h1>CNA Singapore &amp; World News</h1><p class="sub">Singapore and World articles with available audio · rolling 7-day archive · {len(articles)} articles · Updated {updated} SGT</p><button class="playbtn playall" id="playall" type="button">▶ Play all, newest first</button></header><main>{f'<p class="status">{html.escape(status)}</p>' if status else ''}{''.join(sections) if sections else '<p>No CNA Singapore or World articles with available audio were found in the saved archive.</p>'}</main><div class="bar" id="bar" hidden><div class="bar-inner"><div class="bar-title" id="barTitle"></div><div class="bar-controls"><button id="prev" type="button" aria-label="Previous">⏮</button><button id="toggle" type="button" aria-label="Pause">⏸</button><button id="next" type="button" aria-label="Next">⏭</button><div class="bar-pos" id="barPos"></div></div><div class="bar-seek"><span class="time" id="cur">0:00</span><input id="seek" type="range" min="0" max="0" step="1" value="0" aria-label="Position"><span class="time" id="total">0:00</span></div></div></div><audio id="player" preload="none"></audio><script src="assets/player.js"></script></body></html>\n'''


def main() -> int:
    old = [a for a in load_archive() if in_window(a.get("published")) and a.get("audio")]
    old_by_url = {a["url"]: a for a in old if a.get("url")}
    status = ""
    discoveries: dict[str, list[dict]] = {}
    discovery_errors: list[str] = []
    try:
        discoveries["search"] = algolia_items()
    except Exception as exc:
        discovery_errors.append(f"search: {exc}")
        log(f"ERROR search: {exc}")
    try:
        discoveries["sitemap"] = sitemap_items(fetch(SITEMAP))
    except Exception as exc:
        discovery_errors.append(f"sitemap: {exc}")
        log(f"ERROR sitemap: {exc}")
    if not discoveries:
        status = "Last update could not reach CNA. Showing the previously saved archive."
        log(f"ERROR discovery unavailable; retained={len(old)}")
        OUT.write_text(render(old, status), encoding="utf-8")
        return 1
    candidates_by_url: dict[str, dict] = {}
    for source, items in discoveries.items():
        for item in items:
            candidates_by_url[item["url"]] = item
        log(f"OK {source} candidates={len(items)}")
    candidates = list(candidates_by_url.values())
    accepted, skipped, failures = 0, 0, 0
    merged = dict(old_by_url)
    for item in candidates:
        if item["url"] in old_by_url:
            continue
        try:
            metadata = article_metadata(fetch(item["url"]), item)
            if not metadata["podcast_id"] or not metadata["project_id"]:
                skipped += 1
                log(f"SKIP no BeyondWords player: {item['url']}")
                continue
            audio, duration = resolve_audio(metadata["project_id"], metadata["podcast_id"])
            if not audio:
                skipped += 1
                log(f"SKIP no direct MP3: {item['url']}")
                continue
            published = metadata["published"] if in_window(metadata["published"]) else item["published"]
            merged[item["url"]] = {"url": item["url"], "title": metadata["title"] or item["title"],
                "description": metadata["description"], "published": published, "author": metadata["author"],
                "image": metadata["image"], "source": "CNA", "podcast_id": metadata["podcast_id"],
                "project_id": metadata["project_id"], "audio": audio, "duration_ms": duration}
            accepted += 1
        except Exception as exc:  # Individual article failures must not erase archive.
            failures += 1
            log(f"ERROR article {item['url']}: {exc}")
        time.sleep(0.12)
    archive = [a for a in merged.values() if in_window(a.get("published")) and a.get("audio", "").startswith("https://cdn.beyondwords.io/") and ".mp3" in a.get("audio", "")]
    archive.sort(key=lambda a: parse_date(a.get("published")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if not archive and old:
        status = "Update returned no usable CNA audio articles; showing the previously saved archive."
        archive = old
        log(f"ERROR empty result; retained={len(old)}")
    else:
        atomic_write(DATA, json.dumps(archive, ensure_ascii=False, indent=2) + "\n")
        log(f"OK discovered={len(candidates)} accepted={accepted} skipped={skipped} failures={failures} retained={len(archive)}")
    OUT.write_text(render(archive, status), encoding="utf-8")
    return 0 if not status else 1


if __name__ == "__main__":
    sys.exit(main())
