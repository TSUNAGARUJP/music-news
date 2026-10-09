"""YouTubeの再生数を取って「どれだけ話題か」を測る。

- 音楽の人気チャート（米国・英国）：1回1ユニット
- 話題ごとの関連動画検索：1回100ユニット（設定で回数を制限）
無料枠は1日10,000ユニットなので、初期設定（検索15回）なら十分収まります。
"""
import urllib.parse
from datetime import timedelta

from util import http_json, log, now_utc, parse_date

API = "https://www.googleapis.com/youtube/v3/"
MIN_VIEWS = 10_000  # これ未満の動画は無関係な可能性が高く、話題性を低く見せてしまうので付けない


def _get(endpoint: str, key: str, **params) -> dict:
    params["key"] = key
    return http_json(API + endpoint + "?" + urllib.parse.urlencode(params), timeout=20)


def _video_info(item: dict) -> dict:
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    published = parse_date(sn.get("publishedAt"))
    hours = None
    if published:
        hours = max(1, round((now_utc() - published).total_seconds() / 3600))
    vid = item["id"] if isinstance(item["id"], str) else item["id"].get("videoId")
    return {
        "id": vid,
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "published": sn.get("publishedAt"),
        "hours_since": hours,
        "views": int(st["viewCount"]) if st.get("viewCount") else None,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def music_chart(key: str, regions: list[str]) -> list[dict]:
    """YouTubeの音楽人気チャート（地域ごと上位50本）。"""
    out = []
    for region in regions:
        try:
            data = _get("videos", key, part="snippet,statistics", chart="mostPopular",
                        videoCategoryId="10", regionCode=region, maxResults=50)
            for rank, item in enumerate(data.get("items", []), start=1):
                info = _video_info(item)
                info.update(region=region, rank=rank)
                out.append(info)
        except Exception as e:  # noqa: BLE001
            log(f"  YouTubeチャート取得失敗（{region}）: {e}")
    return out


def _matches(artists: list[str], video: dict) -> bool:
    hay = (video["title"] + " " + video["channel"]).lower()
    return any(a.lower() in hay for a in artists if len(a) >= 2)


def chart_hits(artists: list[str], chart: list[dict]) -> list[dict]:
    """チャートに入っている関連動画（地域ごとに最上位の1本）。"""
    best: dict[str, dict] = {}
    for v in chart:
        if _matches(artists, v) and (v["region"] not in best or v["rank"] < best[v["region"]]["rank"]):
            best[v["region"]] = v
    return sorted(best.values(), key=lambda v: v["rank"])


def related_video(key: str, query: str, artists: list[str], days: int = 7) -> dict | None:
    """話題に関連する直近の動画のうち、再生数がいちばん多いもの。"""
    if not artists:
        return None
    after = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        found = _get("search", key, part="snippet", q=query, type="video", order="viewCount",
                     publishedAfter=after, maxResults=8)
        ids = [i["id"]["videoId"] for i in found.get("items", []) if i.get("id", {}).get("videoId")]
        if not ids:
            return None
        stats = _get("videos", key, part="snippet,statistics", id=",".join(ids))
        videos = [_video_info(i) for i in stats.get("items", [])]
        videos = [v for v in videos if _matches(artists, v) and v["views"] is not None]
        if not videos:
            return None
        best = max(videos, key=lambda v: v["views"])
        if best["views"] < MIN_VIEWS:
            return None
        best["views_per_hour"] = round(best["views"] / best["hours_since"]) if best["hours_since"] else None
        return best
    except Exception as e:  # noqa: BLE001
        log(f"  YouTube検索失敗（{query}）: {e}")
        return None
