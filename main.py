"""洋楽ネタ デイリーまとめ：毎日の処理の本体。

  python3 main.py              … 記事を集めてAIで選び、data/ に保存してサイトを作る
  python3 main.py --send-line  … 作ったまとめをLINEに送る（サイト公開のあとに実行）
"""
import argparse
import json
import os
import re
import sys
import tomllib
import traceback
from datetime import date, timedelta
from pathlib import Path

import ai
import collect
import notify
import youtube
from site_builder import build_site
from util import log, now_utc, today_jst

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SITE = ROOT / "site"
GENRES = ai.GENRES


# ---------- 設定と記録 ----------

def load_config() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_state() -> dict:
    p = DATA / "state.json"
    if p.exists():
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            s.setdefault("seen", {})
            s.setdefault("feeds", {})
            return s
        except json.JSONDecodeError:
            pass
    return {"seen": {}, "feeds": {}}


def save_state(state: dict, today: str) -> None:
    limit = (date.fromisoformat(today) - timedelta(days=10)).isoformat()
    state["seen"] = {u: d for u, d in state["seen"].items() if d >= limit}
    (DATA / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def recent_picks(today: str, days: int = 7) -> list[str]:
    out = []
    d0 = date.fromisoformat(today)
    for k in range(1, days + 1):
        p = DATA / f"{(d0 - timedelta(days=k)).isoformat()}.json"
        if not p.exists():
            continue
        try:
            day = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        labels = {c["id"]: c["label"] for c in day.get("candidates", [])}
        for t in day.get("picks", {}).get("top", []):
            out.append(f"{t.get('title_ja', '')}（{labels.get(t.get('id'), '')}）")
        for t in day.get("picks", {}).get("japan_tours", []):
            out.append(f"来日：{t.get('title_ja', '')}")
        for g, rs in day.get("picks", {}).get("runners_up", {}).items():
            for t in rs:
                out.append(f"{'雑学ネタ' if g == 'Other' else '次点'}：{t.get('title_ja', '')}")
    return out


# ---------- 話題のまとめと候補選び ----------

def _uniq_outlets(names) -> list[str]:
    """「MTV JAPAN」と「MTV Japan」のような表記ゆれを1媒体として数える。"""
    seen: dict[str, str] = {}
    for n in names:
        seen.setdefault(n.lower().replace(" ", ""), n)
    return sorted(seen.values())


_TOUR_RE = re.compile(r"\b(japan|tokyo|osaka|nagoya|fukuoka)\b.{0,40}\b(tour|concerts?|shows?|gigs?|debut|dates?)\b"
                      r"|\b(tour|concerts?|shows?|gigs?)\b.{0,40}\b(japan|tokyo|osaka|nagoya|fukuoka)\b", re.I)


_JP_PLACE = r"(東京|大阪|名古屋|福岡|札幌|横浜|神戸|京都|仙台|広島|岡山|幕張|武道館|東京ドーム|京セラドーム)"
_JP_TOUR_RE = re.compile(r"来日|日本公演|ジャパン・?ツアー"
                         rf"|{_JP_PLACE}.{{0,30}}(公演|ツアー|出演|開催)|(公演|ツアー|出演).{{0,30}}{_JP_PLACE}")


def _looks_like_tour(kind: str, label: str, arts: list[dict], visit_source: bool) -> bool:
    """来日の話題かどうか。AIの判定に加えて、見出しの言葉（来日、日本の地名＋公演など）からも拾う。
    来日の検索で見つかっただけの記事（海外での公演など）は来日扱いしない。"""
    if kind == "japan_tour":
        return True
    if kind == "evergreen":  # 「来日を振り返る」のような雑学記事は除く
        return False
    if _TOUR_RE.search(label):
        return True
    return any(_JP_TOUR_RE.search(a["title"]) for a in arts if a.get("region") == "JP")


def build_stories(articles: list[dict], clusters: list[dict]) -> list[dict]:
    stories = []
    for n, cl in enumerate(clusters):
        arts = sorted((articles[i] for i in cl["article_ids"]),
                      key=lambda a: a["published"] or "", reverse=True)
        outlets = _uniq_outlets(a["outlet"] for a in arts)
        jp = _uniq_outlets(a["outlet"] for a in arts if a.get("region") == "JP")
        mainstream = _uniq_outlets(a["outlet"] for a in arts if a.get("group") == "一般")
        visit_source = any(a.get("group") == "来日" for a in arts)
        is_tour = _looks_like_tour(cl["kind"], cl["label"], arts, visit_source)
        stories.append({
            "id": f"s{n}", "label": cl["label"], "artists": cl["artists"], "genre": cl["genre"],
            "kind": cl["kind"], "outlets": outlets, "n_outlets": len(outlets),
            "jp_outlets": jp, "mainstream_outlets": mainstream,
            "visit_source": visit_source, "is_tour": is_tour,
            "n_overseas": len(outlets) - len(jp),
            # 点数＝海外の媒体数（一般メディアは2倍）。日本の媒体は「日本でも報じられた」という
            # 補助的な目印として、何媒体あっても＋1だけ
            "score": (len(outlets) - len(jp)) + len(mainstream) + (1 if jp else 0),
            "latest": arts[0]["published"] or "",
            "articles": [{"outlet": a["outlet"], "title": a["title"], "link": a["link"],
                          "published": a["published"], "summary": a["summary"][:240]} for a in arts[:10]],
        })
    stories.sort(key=lambda s: (s["score"], s["n_outlets"], len(s["articles"]), s["latest"]), reverse=True)
    return stories


def pick_evergreen(stories: list[dict]) -> list[dict]:
    """いつでも使えるネタの候補（雑学・裏話・歴史など）。"""
    return sorted((s for s in stories if s["kind"] == "evergreen"),
                  key=lambda s: (s["score"], s["n_outlets"]), reverse=True)[:10]


def pick_tours(stories: list[dict]) -> list[dict]:
    """来日情報の候補（AIが来日と判定したもの＋来日系の情報源から来た公演・フェスの話題）。"""
    return [s for s in stories if s.get("is_tour")][:15]


def pick_candidates(stories: list[dict], cfg: dict) -> list[dict]:
    g = cfg["general"]
    # ニュースの候補は「海外の媒体が報じた話題」だけ。来日情報と雑学ネタは別枠で選ぶ
    pool = [s for s in stories
            if s["kind"] != "evergreen" and not s["is_tour"] and s["n_overseas"] >= 1
            and (s["genre"] in GENRES or s["n_outlets"] >= 3)]
    chosen = pool[: g["candidate_count"]]
    ids = {s["id"] for s in chosen}
    for genre in GENRES:  # 次点を選べるよう、各ジャンル最低限の候補を確保する
        have = sum(1 for s in chosen if s["genre"] == genre)
        for s in pool:
            if have >= g["min_candidates_per_genre"]:
                break
            if s["genre"] == genre and s["id"] not in ids:
                chosen.append(s)
                ids.add(s["id"])
                have += 1
    return chosen


def enrich_with_youtube(candidates: list[dict], cfg: dict) -> None:
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        log("YouTube APIキー未設定のため、再生数の取得をスキップします")
        return
    yt = cfg["youtube"]
    chart = youtube.music_chart(key, yt["chart_regions"])
    log(f"YouTube音楽チャート：{len(chart)}本")
    searches = 0
    for c in candidates:
        c["chart"] = youtube.chart_hits(c["artists"], chart) if c["artists"] else []
        if c["chart"]:
            c["video"] = dict(c["chart"][0])
            continue
        if searches >= yt["max_searches"] or not c["artists"]:
            continue
        searches += 1
        c["video"] = youtube.related_video(key, c["label"][:100], c["artists"], days=yt["search_days"])
    log(f"YouTube検索：{searches}回")


def estimate_cost(costs: list[dict], prices: dict) -> float:
    usd = 0.0
    for c in costs:
        p_in, p_out = prices.get(c["model"], [0, 0])
        usd += c["input_tokens"] / 1e6 * p_in + c["output_tokens"] / 1e6 * p_out
    return round(usd, 4)


# ---------- 毎日の処理 ----------

def make_today(cfg: dict, state: dict, today: str) -> dict:
    g, models = cfg["general"], cfg["models"]
    seen_before = {u for u, d in state["seen"].items() if d < today}

    log(f"記事を集めています（{len(cfg['sources'])}媒体）")
    articles, health = collect.collect_all(cfg["sources"], g["lookback_hours"], seen_before, state["feeds"])
    articles = articles[: g["max_articles"]]
    log(f"新着記事：{len(articles)}本")
    if len(articles) < 5:
        raise RuntimeError(f"新着記事が{len(articles)}本しか取得できませんでした。媒体のRSS取得状況を確認してください。")

    costs: list[dict] = []
    log(f"段階1：話題をグループ化（{models['cluster']}）")
    clusters = ai.cluster_articles(articles, models["cluster"], costs)
    stories = build_stories(articles, clusters)
    log(f"  話題：{len(stories)}件（複数媒体が報じたもの {sum(1 for s in stories if s['n_outlets'] > 1)}件）")

    candidates = pick_candidates(stories, cfg)
    cand_ids = {c["id"] for c in candidates}
    tours = pick_tours(stories)  # 来日情報はトップの候補と重なっていても、すべてAIに渡す
    extra_tours = [t for t in tours if t["id"] not in cand_ids]
    evergreen = pick_evergreen(stories)
    extra_tours += [e for e in evergreen if e["id"] not in {t["id"] for t in extra_tours}]
    log(f"  候補：{len(candidates)}件、来日情報の候補：{len(tours)}件、いつでも使えるネタの候補：{len(evergreen)}件")
    enrich_with_youtube(candidates, cfg)
    recent = recent_picks(today)

    log(f"段階2：選定と執筆（{models['select']}）")
    picks = ai.select_picks(candidates, tours, evergreen, cfg, recent, models["select"], models.get("select_effort", ""), costs)

    compare = None
    if models.get("compare"):
        log(f"比較用：{models['compare']} でも選定")
        try:
            compare = ai.select_picks(candidates, tours, evergreen, cfg, recent, models["compare"],
                                      models.get("compare_effort", ""), costs)
        except Exception as e:  # noqa: BLE001
            log(f"  比較用の選定は失敗しました（本番には影響なし）: {e}")

    shown = cand_ids | {t["id"] for t in tours}
    others = [s for s in stories if s["id"] not in shown and s["n_outlets"] >= 2][:40]
    for s in candidates + extra_tours:
        s["shortlisted"] = True
    for s in others:
        s["shortlisted"] = False

    for a in articles:
        state["seen"][a["url_key"]] = today

    usd = estimate_cost(costs, cfg.get("prices", {}))
    log(f"AI費用の目安：${usd:.3f}")
    return {
        "date": today,
        "generated_at": now_utc().isoformat(),
        "article_count": len(articles),
        "picks": picks,
        "compare": compare,
        "candidates": candidates + extra_tours + others,
        "health": health,
        "cost": {"usd": usd, "jpy_per_usd": g.get("jpy_per_usd", 150), "detail": costs},
    }


def run_url() -> str:
    if os.environ.get("GITHUB_RUN_ID"):
        return (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    return ""


def line_enabled() -> bool:
    return os.environ.get("SEND_LINE", "true").strip().lower() != "false"


def cmd_build() -> int:
    cfg = load_config()
    DATA.mkdir(exist_ok=True)
    today = today_jst()
    state = load_state()
    try:
        day = make_today(cfg, state, today)
        (DATA / f"{today}.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
        save_state(state, today)
        log(f"保存しました：data/{today}.json")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        save_state(state, today)  # 見つけたRSSのURLは失敗時も記録しておく
        build_site(DATA, SITE, cfg["general"]["site_title"])
        if line_enabled():
            notify.send_error(str(e), run_url())
        return 1
    build_site(DATA, SITE, cfg["general"]["site_title"])
    log("サイトを作成しました")
    return 0


def cmd_send_line() -> int:
    if not line_enabled():
        log("LINE送信はオフに設定されています")
        return 0
    today = today_jst()
    p = DATA / f"{today}.json"
    if not p.exists():
        log("今日のまとめが見つかりません")
        return 1
    day = json.loads(p.read_text(encoding="utf-8"))
    base = os.environ.get("PAGE_URL", "").strip() or load_config()["general"].get("site_url", "")
    if base and not base.endswith("/"):
        base += "/"
    page = f"{base}days/{today}.html" if base else "（サイトURL未設定）"
    try:
        notify.push_text(notify.build_digest(day, page))
    except Exception as e:  # noqa: BLE001
        log(f"LINE送信に失敗しました: {e}")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-line", action="store_true", help="今日のまとめをLINEに送る")
    args = ap.parse_args()
    sys.exit(cmd_send_line() if args.send_line else cmd_build())
