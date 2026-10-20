"""スクール生向け「今週の音楽話題まとめ」をDiscordに投稿する（週1回）。

毎日のまとめ（data/YYYY-MM-DD.json）の1週間分から、洋楽ニュースとDJ・クラブの話題を選び、
Discordのウェブフックで投稿します。記事の集め直しはしません。

  python3 weekly.py             … 選んでDiscordに投稿し、data/weekly/ に記録する
  python3 weekly.py --dry-run   … 投稿せず、投稿内容をログに表示するだけ
"""
import argparse
import json
import os
import sys
import time
import tomllib
import traceback
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import ai
import notify
from util import fmt_views, log, today_jst

ROOT = Path(__file__).parent
DATA = ROOT / "data"
WEEKLY = DATA / "weekly"
WEEKDAYS = "月火水木金土日"
COLORS = {"Dance": 0x0078BF, "POP": 0xFF48B0, "Hip-Hop": 0xFFD900}
UA = "DiscordBot (https://github.com/TSUNAGARUJP/music-news, 1.0)"  # 既定の名前だと拒否されることがある


# ---------- 1週間分の話題を集める ----------

def load_week(days: int, today: str) -> tuple[list[dict], list[str]]:
    end = date.fromisoformat(today)
    files = [DATA / f"{(end - timedelta(days=k)).isoformat()}.json" for k in range(days)]
    pool, used_days = [], []
    for p in sorted(f for f in files if f.exists()):
        try:
            day = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        used_days.append(day["date"])
        picks = day.get("picks", {})
        flag = {t["id"]: "top" for t in picks.get("top", [])}
        for rs in picks.get("runners_up", {}).values():
            for r in rs:
                flag.setdefault(r["id"], "runner")
        for t in picks.get("japan_tours", []):
            flag.setdefault(t["id"], "tour")
        for c in day.get("candidates", []):
            if c.get("kind") == "evergreen":
                continue
            jp = c.get("jp_outlets", [])
            overseas = c.get("n_overseas", c["n_outlets"] - len(jp))
            tour = c.get("is_tour", c.get("kind") == "japan_tour")
            if tour and c.get("genre") != "Dance":
                continue  # 来日情報はDJ・ダンス系だけ（DJ・クラブ枠の候補）
            if not tour and overseas < 1:
                continue  # 日本の媒体だけが報じた話題は対象外
            if not (c["id"] in flag or c["n_outlets"] >= 2 or c.get("genre") == "Dance"):
                continue  # 単独の小さな話題は、DJ・クラブ枠の候補になるダンス系だけ残す
            pool.append({**c, "ref": f"{day['date'][5:]}:{c['id']}", "date": day["date"],
                         "daily_pick": flag.get(c["id"], ""), "is_tour": tour})
    return pool, used_days


def _brief(c: dict) -> dict:
    b = {"ref": c["ref"], "label": c["label"], "artists": c.get("artists", []), "genre": c.get("genre"),
         "kind": "japan_tour" if c["is_tour"] else c.get("kind"), "outlets_count": c["n_outlets"],
         "japan_outlets": len(c.get("jp_outlets", [])), "mainstream": c.get("mainstream_outlets", []),
         "daily_pick": c["daily_pick"],
         "headlines": [a["title"] for a in c["articles"][:2]],
         "summary": next((a["summary"][:120] for a in c["articles"] if a.get("summary")), "")}
    if c.get("video") and c["video"].get("views"):
        b["youtube_views"] = c["video"]["views"]
    return b


def recent_weekly(today: str) -> list[str]:
    """直近2回分の週まとめで紹介した話題（重複を避けるため）。"""
    out = []
    end = date.fromisoformat(today)
    for p in sorted(WEEKLY.glob("????-??-??.json"), reverse=True)[:3]:
        if p.stem >= today or date.fromisoformat(p.stem) < end - timedelta(days=15):
            continue
        try:
            w = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out += [i["title_ja"] for i in w.get("news", []) + w.get("dj", [])]
    return out


# ---------- AIで選んで書く ----------

SYSTEM = """あなたは、DJスクールの講師アシスタントです。1週間の音楽ニュースから話題を厳選し、
スクール生向けにDiscordで紹介する文章を書きます。

守ること：
- 書くのは、渡された見出し・概要・数字から言えることだけ。推測を事実のように書かない。
  不確かなことは「〜と報じられています」と書く。数字は渡されたものだけを使う。
- アーティスト名・曲名・アルバム名は英語表記のまま書く。
- 文体は「です・ます」。
- 返答はJSONのみ。前置きや説明は書かない。
- JSONの文字列の中では半角の二重引用符（"）を使わない。曲名などは「」や『』で囲む。"""

PROMPT = """# 読者
{audience}

# 選び方
{criteria}

共通のルール：
- 「news」は{news_count}件、「dj」は{dj_count}件。newsとdjで同じ話題を選ばない。
- 同じ出来事が別の日の候補にも出ていたら、1つの話題として扱う（refはどれか1つ）。何日も続けて報じられた話題は、それだけ大きな話題と考える。
- kind が japan_tour の候補（DJ・ダンス系アーティストの来日）は、djでだけ選んでよい。
- 「最近の週まとめで紹介した話題」と同じものは選ばない。
- 訃報・事件・裁判は、音楽的に大きな意味があるものだけ。茶化さず事実を伝える。
- 日本のアーティストだけの話題は選ばない。

# 書き方
- intro：今週の傾向を1〜2文（80字以内）。
- title_ja：何が起きたかが一目で分かる見出し（40字以内）。
- summary_ja：何が起きたかを2〜3文（120字以内）。
- newsの point_ja：なぜ話題か1文（60字以内）。媒体数や再生数などの根拠を入れる。
- djの dj_point_ja：DJ・スクール生にとってのポイント1文（60字以内）。選曲、プレイ、DJとしての活動などの観点で。

# 返答の形（JSONのみ）
{{"intro":"...","news":[{{"ref":"10-13:s3","title_ja":"...","summary_ja":"...","point_ja":"..."}}],"dj":[{{"ref":"10-15:s8","title_ja":"...","summary_ja":"...","dj_point_ja":"..."}}]}}

# 最近の週まとめで紹介した話題
{recent}

# 今週の候補（{start}〜{end}の毎日のまとめから。daily_pick：top＝その日のトップ、runner＝次点、tour＝来日情報）
{candidates}
"""


def select(pool: list[dict], wcfg: dict, recent: list[str], start: str, end: str, costs: list) -> dict:
    prompt = PROMPT.format(
        audience=wcfg["audience"].strip(), criteria=wcfg["criteria"].strip(),
        news_count=wcfg["news_count"], dj_count=wcfg["dj_count"],
        recent="\n".join(f"- {t}" for t in recent) or "（なし）",
        start=start, end=end, candidates=ai._compact([_brief(c) for c in pool]))
    data = ai.ask_json(wcfg["model"], SYSTEM, prompt, max_tokens=16000, effort=wcfg.get("effort", ""), costs=costs)

    by_ref = {c["ref"]: c for c in pool}
    used, seen_labels = set(), set()

    def take(items, n, allow_tour):
        out = []
        for it in items or []:
            c = by_ref.get(it.get("ref"))
            if not c or it["ref"] in used or not it.get("title_ja"):
                continue
            if c["is_tour"] and not allow_tour:
                continue
            key = c["label"].lower()
            if key in seen_labels:
                continue
            used.add(it["ref"])
            seen_labels.add(key)
            out.append({**it, "story": c})
            if len(out) >= n:
                break
        return out

    news = take(data.get("news"), wcfg["news_count"], allow_tour=False)
    dj = take(data.get("dj"), wcfg["dj_count"], allow_tour=True)
    if not news and not dj:
        raise ai.ClaudeError("AIが話題を選べませんでした")
    return {"intro": str(data.get("intro", "")), "news": news, "dj": dj}


# ---------- Discordの投稿を組み立てる ----------

def _jd(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


def _links(c: dict) -> str:
    """元記事のリンク（媒体ごとに1つ、Googleニュース経由より直接のリンクを優先）。"""
    best: dict[str, str] = {}
    for a in c.get("articles", []):
        cur = best.get(a["outlet"])
        if cur is None or ("news.google.com" in cur and "news.google.com" not in a["link"]):
            best[a["outlet"]] = a["link"]
    items = list(best.items())
    text = "・".join(f"[{o}]({u})" for o, u in items[:3])
    if len(items) > 3:
        text += f" ほか{len(items) - 3}媒体"
    return text


def build_payload(result: dict, wcfg: dict, start: str, end: str) -> dict:
    embeds = []
    sections = [("洋楽ニュース", result["news"], "point_ja", "なぜ話題？"),
                ("DJ・クラブ", result["dj"], "dj_point_ja", "DJ的ポイント")]
    n = 0
    for section, items, key, label in sections:
        for it in items:
            n += 1
            c = it["story"]
            lines = [it.get("summary_ja", "")]
            if it.get(key):
                lines.append(f"\n**{label}**　{it[key]}")
            src = _links(c)
            if src:
                lines.append(f"\n元記事：{src}")
            v = c.get("video") or {}
            if v.get("views") and v.get("url"):
                vt = v["title"] if len(v["title"]) <= 50 else v["title"][:48].rstrip() + "…"
                lines.append(f"関連動画：[{vt}]({v['url']})（{fmt_views(v['views'])}）")
            first = next((a["link"] for a in c.get("articles", []) if "news.google.com" not in a["link"]),
                         (c.get("articles") or [{}])[0].get("link"))
            embed = {
                "author": {"name": section},
                "title": f"{n}. {it['title_ja']}"[:256],
                "description": "\n".join(lines)[:4000],
                "color": COLORS.get(c.get("genre"), 0x8A8F98),
            }
            if first:
                embed["url"] = first
            embeds.append(embed)
    content = f"## {wcfg['title']}（{_jd(start)}〜{_jd(end)}）\n"
    subtitle = str(wcfg.get("subtitle", "")).strip()
    if subtitle:
        content += f"{subtitle}\n"
    content += result["intro"]
    mention = str(wcfg.get("mention", "")).strip()
    if mention:
        content = f"{mention}\n{content}"
    # @everyone / @here を書いたときだけ、その通知を有効にする（本文中の誤ったメンションは飛ばさない）
    parse = ["everyone"] if mention in ("@everyone", "@here") else []
    return {"username": wcfg.get("username", "今週の音楽話題"), "content": content[:2000],
            "embeds": embeds[:10], "allowed_mentions": {"parse": parse}}


def post_discord(payload: dict) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_URL が設定されていません（GitHubのSecretsに登録してください）")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(url + ("&" if "?" in url else "?") + "wait=true", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            log("  Discordに投稿しました")
            return
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < 2:  # 送りすぎ。指定された秒数だけ待って再送
                try:
                    wait = float(json.loads(detail).get("retry_after", 5))
                except (ValueError, json.JSONDecodeError):
                    wait = 5
                time.sleep(min(wait, 30) + 1)
                continue
            raise RuntimeError(f"Discordへの投稿に失敗しました（{e.code}）: {detail}") from None


# ---------- 本体 ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="投稿せず内容を表示する")
    args = ap.parse_args()
    dry = args.dry_run or os.environ.get("DRY_RUN", "").lower() == "true"

    wcfg = tomllib.loads((ROOT / "weekly.toml").read_text(encoding="utf-8"))["weekly"]
    prices = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8")).get("prices", {})
    today = today_jst()
    start = (date.fromisoformat(today) - timedelta(days=wcfg["days"] - 1)).isoformat()
    try:
        pool, used_days = load_week(wcfg["days"], today)
        log(f"{len(used_days)}日分のまとめから、候補{len(pool)}件")
        if len(pool) < 5:
            raise RuntimeError(f"候補が{len(pool)}件しかありません（毎日のまとめが動いているか確認してください）")
        costs: list = []
        result = select(pool, wcfg, recent_weekly(today), start, today, costs)
        payload = build_payload(result, wcfg, start, today)
        usd = sum(c["input_tokens"] / 1e6 * prices.get(c["model"], [0, 0])[0]
                  + c["output_tokens"] / 1e6 * prices.get(c["model"], [0, 0])[1] for c in costs)
        log(f"AI費用の目安：${usd:.3f}")
        if dry:
            log("確認モードのため投稿しません。投稿内容：")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        post_discord(payload)
        WEEKLY.mkdir(parents=True, exist_ok=True)
        record = {"date": today, "start": start, "intro": result["intro"], "cost_usd": round(usd, 4),
                  "news": [{k: v for k, v in i.items() if k != "story"} for i in result["news"]],
                  "dj": [{k: v for k, v in i.items() if k != "story"} for i in result["dj"]]}
        (WEEKLY / f"{today}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if not dry:
            try:
                notify.push_text(f"■ 今週の音楽話題（Discord）：投稿に失敗しました\n\n{str(e)[:600]}")
            except Exception:  # noqa: BLE001
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
