"""Claude APIとのやりとり。

  段階1（下ごしらえ）：見出しを読み、同じ話題をグループ化してジャンルを振り分ける
  段階2（選定と執筆）：候補からトップ3と次点を選び、日本語でまとめとリール案を書く
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from util import log

API_URL = "https://api.anthropic.com/v1/messages"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}
GENRES = ("Dance", "POP", "Hip-Hop")


class ClaudeError(RuntimeError):
    pass


def call_claude(model: str, system: str, user: str, max_tokens: int,
                effort: str = "") -> tuple[str, dict]:
    """Claudeに1回問い合わせて、返答テキストと使用トークン数を返す（ストリーミング受信）。"""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY が設定されていません")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "stream": True,
    }
    if effort:
        body["output_config"] = {"effort": effort}
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    waits = [15, 45, 90]
    for attempt in range(len(waits) + 1):
        try:
            req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers=headers)
            return _read_stream(urllib.request.urlopen(req, timeout=180))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            if e.code in RETRY_STATUS and attempt < len(waits):
                log(f"  Claude API 混雑（{e.code}）。{waits[attempt]}秒後に再試行")
                time.sleep(waits[attempt])
                continue
            raise ClaudeError(f"Claude API エラー {e.code}: {detail}") from None
        except (ClaudeError, TimeoutError, urllib.error.URLError) as e:
            if attempt < len(waits) and ("overloaded" in str(e) or not isinstance(e, ClaudeError)):
                log(f"  Claude API 一時エラー（{e}）。{waits[attempt]}秒後に再試行")
                time.sleep(waits[attempt])
                continue
            raise
    raise ClaudeError("Claude API に接続できませんでした")


def _read_stream(resp) -> tuple[str, dict]:
    text_parts: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    stop_reason = None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "message_start":
                u = ev.get("message", {}).get("usage", {})
                usage["input_tokens"] = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                         + u.get("cache_creation_input_tokens", 0))
            elif t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                text_parts.append(ev["delta"].get("text", ""))
            elif t == "message_delta":
                stop_reason = ev.get("delta", {}).get("stop_reason") or stop_reason
                usage["output_tokens"] = ev.get("usage", {}).get("output_tokens", usage["output_tokens"])
            elif t == "error":
                raise ClaudeError(f"Claude API ストリームエラー: {ev.get('error')}")
    if stop_reason == "max_tokens":
        log("  注意：出力が上限で途中終了しました（max_tokensを増やすと改善します）")
    return "".join(text_parts), usage


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("返答にJSONが見つかりません")
    return json.loads(text[start:end + 1])


def ask_json(model: str, system: str, user: str, max_tokens: int, effort: str,
             costs: list) -> dict:
    """JSONで返してもらう。形が崩れていたら1回だけやり直す。"""
    last = None
    for _ in range(2):
        text, usage = call_claude(model, system, user, max_tokens, effort)
        costs.append({"model": model, **usage})
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            log(f"  返答のJSONが読めなかったので再試行します（{e}）")
    raise ClaudeError(f"AIの返答を読み取れませんでした: {last}")


# ---------- 段階1：グループ化 ----------

CLUSTER_SYSTEM = (
    "You are a meticulous music news desk editor. You group headlines from many outlets "
    "into stories, so the team can see which stories are covered most widely."
)

CLUSTER_RULES = """Group the articles below into stories.

Rules:
- A story is ONE underlying event (e.g. "Artist X announces album Y"). Different events about the same artist are different stories.
- Put every article that is about music into exactly one story. Single-article stories are fine.
- Exclude articles that are not news about music: film/TV/gaming, shopping deals, quizzes, generic listicles or retrospectives (e.g. "best albums of 2004"), horoscopes. List their ids in "excluded".
- genre: "Dance" (house, techno, EDM, DJs, electronic, bass), "Hip-Hop" (rap, and R&B that sits close to rap), "POP" (pop, mainstream R&B, K-pop, Latin pop), "Other" (rock, metal, indie rock, country, jazz, classical, etc.). Decide by the main artist's genre.
- kind: one of release, announcement, tour, festival, chart, award, collab, beef, legal, health, death, business, interview, other.
- label: a short, specific English description of the event (max 12 words).
- artists: the main artist or DJ names, as written in the headlines.

Reply with JSON only, no other text:
{"stories":[{"label":"...","artists":["..."],"genre":"POP","kind":"release","ids":[3,17]}],"excluded":[5,9]}

Articles (id, outlet, genre the outlet usually covers, headline — summary):
"""


def cluster_articles(articles: list[dict], model: str, costs: list) -> list[dict]:
    lines = []
    for i, a in enumerate(articles):
        summary = a["summary"][:160]
        lines.append(f"[{i}] ({a['outlet']} | {a['genre_hint']}) {a['title']} — {summary}")
    data = ask_json(model, CLUSTER_SYSTEM, CLUSTER_RULES + "\n".join(lines),
                    max_tokens=16000, effort="", costs=costs)

    used: set[int] = set()
    excluded = {i for i in data.get("excluded", []) if isinstance(i, int)}
    stories = []
    for s in data.get("stories", []):
        ids = [i for i in s.get("ids", []) if isinstance(i, int) and 0 <= i < len(articles) and i not in used]
        if not ids:
            continue
        used.update(ids)
        genre = s.get("genre") if s.get("genre") in GENRES + ("Other",) else "Other"
        stories.append({"label": str(s.get("label", ""))[:160], "artists": [str(x) for x in s.get("artists", [])][:5],
                        "genre": genre, "kind": str(s.get("kind", "other")), "article_ids": ids})
    # AIが振り分け忘れた記事は、1記事だけの話題として残す
    for i, a in enumerate(articles):
        if i not in used and i not in excluded:
            hint = a["genre_hint"] if a["genre_hint"] in GENRES else "Other"
            stories.append({"label": a["title"][:160], "artists": [], "genre": hint,
                            "kind": "other", "article_ids": [i]})
    return stories


# ---------- 段階2：選定と執筆 ----------

SELECT_SYSTEM = """あなたは、日本の洋楽インフルエンサー兼DJスクール運営者の専属リサーチャーです。
海外の音楽ニュースの候補から、Instagramリールのネタとして使える話題を選び、日本語でまとめます。

守ること：
- 書くのは、渡された見出し・概要・数字から言えることだけ。推測を事実のように書かない。
  不確かなことは「〜と報じられている」「〜の可能性」と書く。数字は渡されたものだけを使う。
- アーティスト名・曲名・アルバム名は英語表記のまま書く。
- 返答はJSONのみ。前置きや説明は書かない。"""

SELECT_TEMPLATE = """# 視聴者と選定基準
{audience}

{criteria}

# 選び方
- 「top」：ジャンルを問わず、今日いちばんリールにする価値がある話題を{top_n}件。重要度（報じた媒体の数、関連動画の再生数と伸び）と、リールでの伸びやすさの両方で判断する。
- 「runners_up」：topに入らなかったものから、Dance・POP・Hip-Hopそれぞれ最大{runners}件。該当がなければ空でよい。
- 過去7日に取り上げた話題（下に記載）は、大きな続報がない限り選ばない。
- genre が "Other" の話題は、よほど大きなニュースのときだけtopに入れてよい（runners_upには入れない）。

# 各項目の書き方
- title_ja：何が起きたかが一目で分かる見出し。40字以内。
- summary_ja：何が起きたか。2〜3文。
- why_ja：なぜ話題か。根拠（媒体数、再生数、チャート順位など）を1つ以上含めて1〜2文。
- line_point：LINE通知用の要点。60字以内。
- reels：リール案を2つ。hook＝冒頭3秒で言うひとこと（30字以内）、angle＝構成や見せ方（80字以内）。2案は切り口を変える。
- runners_upの note：何が起きたか1文（50字以内）。
- overview：今日の全体の傾向を1〜2文。

# 返答の形（JSONのみ）
{{"overview":"...","top":[{{"id":"s3","title_ja":"...","genre":"Hip-Hop","summary_ja":"...","why_ja":"...","line_point":"...","reels":[{{"hook":"...","angle":"..."}},{{"hook":"...","angle":"..."}}]}}],"runners_up":{{"Dance":[{{"id":"s8","title_ja":"...","note":"..."}}],"POP":[],"Hip-Hop":[]}}}}

# 過去7日に取り上げた話題
{recent}

# 今日の候補（idは返答でもそのまま使う）
{candidates}
"""


def _candidate_brief(c: dict) -> dict:
    brief = {
        "id": c["id"], "label": c["label"], "artists": c["artists"], "genre": c["genre"],
        "kind": c["kind"], "outlets_count": c["n_outlets"], "outlets": c["outlets"],
        "headlines": [a["title"] for a in c["articles"][:6]],
        "summaries": [a["summary"][:220] for a in c["articles"][:3] if a["summary"]],
    }
    if c.get("video"):
        v = c["video"]
        brief["youtube"] = {"title": v["title"], "views": v["views"], "hours_since_upload": v["hours_since"]}
    if c.get("chart"):
        brief["youtube_music_chart"] = [f"{v['region']} {v['rank']}位: {v['title']}" for v in c["chart"]]
    return brief


def select_picks(candidates: list[dict], cfg: dict, recent: list[str], model: str,
                 effort: str, costs: list) -> dict:
    sel = cfg["selection"]
    prompt = SELECT_TEMPLATE.format(
        audience=sel["audience"].strip(),
        criteria=sel["criteria"].strip(),
        top_n=cfg["general"]["top_n"],
        runners=cfg["general"]["runners_up_per_genre"],
        recent="\n".join(f"- {t}" for t in recent) or "（なし）",
        candidates=json.dumps([_candidate_brief(c) for c in candidates], ensure_ascii=False, indent=1),
    )
    data = ask_json(model, SELECT_SYSTEM, prompt, max_tokens=24000, effort=effort, costs=costs)

    valid = {c["id"] for c in candidates}
    top = [t for t in data.get("top", []) if t.get("id") in valid][: cfg["general"]["top_n"]]
    used = {t["id"] for t in top}
    runners = {}
    for g in GENRES:
        items = [r for r in (data.get("runners_up", {}) or {}).get(g, []) or []
                 if r.get("id") in valid and r["id"] not in used]
        runners[g] = items[: cfg["general"]["runners_up_per_genre"]]
        used.update(r["id"] for r in runners[g])
    if not top:
        raise ClaudeError("AIがトップの話題を選べませんでした")
    return {"model": model, "overview": str(data.get("overview", "")), "top": top, "runners_up": runners}
