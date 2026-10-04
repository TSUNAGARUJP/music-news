"""各メディアのRSSから記事を集める。

RSSのURLが変わったり間違っていたりしても止まらないよう、次の順で自動的に探します。
  1. config.toml に書いたURL
  2. 前回うまく取れたURL（data/state.json に記録）
  3. サイトのトップページに書かれているRSSのURL（自動検出）
  4. よくあるRSSの場所（/feed/ や /rss など）
  5. Google ニュースのRSS（そのサイトの記事だけに絞ったもの）
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from util import domain_of, http_get, log, normalize_url, now_utc, parse_date, strip_html

MAX_ITEMS_PER_SOURCE = 40


# ---------- RSS / Atom の読み取り ----------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el, names: tuple[str, ...]) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _atom_link(el) -> str:
    fallback = ""
    for child in el:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            if child.attrib.get("rel", "alternate") == "alternate":
                return href
            fallback = fallback or href
        elif (child.text or "").strip():
            return child.text.strip()
    return fallback


def _parse_xml(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        link = _child_text(el, ("origlink",)) or _atom_link(el) or _child_text(el, ("guid", "id"))
        out.append({
            "title": strip_html(_child_text(el, ("title",)), 300),
            "link": link,
            "date": _child_text(el, ("pubdate", "published", "updated", "date", "issued")),
            "summary": strip_html(_child_text(el, ("description", "summary"))
                                  or _child_text(el, ("encoded", "content")), 400),
        })
    return out


_BLOCK_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _rx(tag: str, body: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", body, re.S | re.I)
    if not m:
        return ""
    s = m.group(1)
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return s.strip()


def _parse_regex(raw: bytes) -> list[dict]:
    """XMLとして壊れているRSS向けの、ゆるい読み取り。"""
    text = raw.decode("utf-8", "replace")
    out = []
    for _, body in _BLOCK_RE.findall(text):
        link = _rx("link", body)
        if not link:
            m = re.search(r"<link\b[^>]*href=[\"']([^\"']+)[\"']", body, re.I)
            link = m.group(1) if m else _rx("guid", body)
        out.append({
            "title": strip_html(_rx("title", body), 300),
            "link": strip_html(link),
            "date": strip_html(_rx("pubDate", body) or _rx("published", body)
                               or _rx("updated", body) or _rx("dc:date", body)),
            "summary": strip_html(_rx("description", body) or _rx("summary", body)
                                  or _rx("content:encoded", body), 400),
        })
    return out


def parse_feed(raw: bytes) -> list[dict]:
    raw = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not raw.startswith(b"<"):
        return []
    try:
        items = _parse_xml(raw)
    except ET.ParseError:
        items = _parse_regex(raw)
    return [i for i in items if i["title"] and i["link"].startswith("http")]


# ---------- RSSの場所を探す ----------

_ALT_RE = re.compile(r"<link\b[^>]*>", re.I)


def discover_feed_urls(site: str) -> list[str]:
    urls: list[str] = []
    try:
        page = http_get(site, timeout=15, retries=0).decode("utf-8", "replace")
        for tag in _ALT_RE.findall(page):
            low = tag.lower()
            if "alternate" in low and ("rss" in low or "atom" in low):
                m = re.search(r"href=[\"']([^\"']+)[\"']", tag, re.I)
                if m:
                    urls.append(urllib.parse.urljoin(site, m.group(1)))
    except Exception:  # noqa: BLE001
        pass
    base = site.rstrip("/")
    for suffix in ("/feed/", "/rss", "/rss.xml", "/feed", "/feed.xml", "/index.xml"):
        urls.append(base + suffix)
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:8]


def google_news_url(query: str) -> str:
    q = urllib.parse.quote(f"{query} when:2d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _try(url: str) -> list[dict]:
    try:
        return parse_feed(http_get(url, timeout=20))
    except Exception:  # noqa: BLE001
        return []


def fetch_source(src: dict, feed_cache: dict) -> tuple[list[dict], dict]:
    name = src["name"]
    tried: list[str] = []
    candidates = []
    if src.get("feed"):
        candidates.append(("設定のURL", src["feed"]))
    cached = feed_cache.get(name)
    if cached and cached not in [c[1] for c in candidates]:
        candidates.append(("前回のURL", cached))

    for method, url in candidates:
        tried.append(url)
        items = _try(url)
        if items:
            feed_cache[name] = url
            return items, {"status": "ok", "method": method, "url": url}

    if src.get("site"):
        for url in discover_feed_urls(src["site"]):
            if url in tried:
                continue
            tried.append(url)
            items = _try(url)
            if items:
                feed_cache[name] = url
                return items, {"status": "ok", "method": "自動検出", "url": url}

    query = src.get("google_news") or (f"site:{domain_of(src['site'])}" if src.get("site") else "")
    if query:
        url = google_news_url(query)
        items = _try(url)
        if items:
            for it in items:  # Googleニュースの見出しは末尾に「 - 媒体名」が付くので外す
                it["title"] = re.sub(r"\s+-\s+[^-]{2,60}$", "", it["title"])
            return items, {"status": "ok", "method": "Googleニュース経由", "url": url}

    return [], {"status": "failed", "method": "取得できず", "url": ""}


# ---------- 全メディアから集める ----------

def collect_all(sources: list[dict], lookback_hours: int, seen_before_today: set[str],
                feed_cache: dict) -> tuple[list[dict], list[dict]]:
    cutoff = now_utc() - timedelta(hours=lookback_hours)

    def work(src):
        items, status = fetch_source(src, feed_cache)
        return src, items, status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, sources))

    articles: list[dict] = []
    health: list[dict] = []
    urls_now: set[str] = set()
    for src, items, status in results:
        kept = 0
        for it in items[:MAX_ITEMS_PER_SOURCE]:
            url = normalize_url(it["link"])
            if url in urls_now or url in seen_before_today:
                continue
            d = parse_date(it["date"])
            if d is not None and d < cutoff:
                continue
            urls_now.add(url)
            kept += 1
            articles.append({
                "source": src["name"],
                "outlet": src.get("outlet") or src["name"],
                "genre_hint": src.get("genre", ""),
                "title": it["title"],
                "link": it["link"],
                "url_key": url,
                "published": d.isoformat() if d else None,
                "summary": it["summary"],
            })
        health.append({"name": src["name"], "genre": src.get("genre", ""), **status,
                       "fetched": len(items), "kept": kept})
        mark = "OK " if status["status"] == "ok" else "NG "
        log(f"  {mark}{src['name']}: {kept}件（{status['method']}）")

    articles.sort(key=lambda a: a["published"] or "", reverse=True)
    return articles, health
