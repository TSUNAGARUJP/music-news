"""共通の小道具（通信・日時・テキスト整形・ログ）"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

JST = timezone(timedelta(hours=9))
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def log(msg: str) -> None:
    stamp = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def http_get(url: str, timeout: int = 20, headers: dict | None = None, retries: int = 1) -> bytes:
    """URLの中身を取ってくる。失敗したら1回だけやり直す。"""
    h = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise last_err  # type: ignore[misc]


def http_json(url: str, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: int = 30) -> dict:
    """JSONのAPIを呼ぶ。エラー時は相手の返答内容つきで例外を出す。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {url.split('?')[0]}: {detail}") from None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str | None, limit: int | None = None) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def parse_date(s: str | None) -> datetime | None:
    """RSS（RFC822）とAtom（ISO 8601）の両方の日付を読む。"""
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_", "ref", "src")


def normalize_url(url: str) -> str:
    """同じ記事を同じURLとして扱うため、追跡用パラメータなどを取り除く。"""
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(_TRACKING_PARAMS)]
    path = p.path.rstrip("/") or "/"
    host = p.netloc.lower().removeprefix("www.")
    return urllib.parse.urlunsplit((p.scheme.lower(), host, path, urllib.parse.urlencode(q), ""))


def domain_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def fmt_views(n: int | None) -> str:
    """再生数を日本語の読みやすい形に（例：8500000 → 850万回）。"""
    if n is None:
        return ""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億回".replace(".0億", "億")
    if n >= 10_000:
        return f"{round(n / 10_000):,}万回"
    return f"{n:,}回"


def die(msg: str) -> None:
    log(msg)
    sys.exit(1)
