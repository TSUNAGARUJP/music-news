"""LINEへの通知（Messaging APIのプッシュメッセージ）。"""
import os
from datetime import date

from util import fmt_views, http_json, log

PUSH_URL = "https://api.line.me/v2/bot/message/push"
WEEKDAYS = "月火水木金土日"


def _creds() -> tuple[str, str]:
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("LINE_USER_ID", "")


def push_text(text: str) -> None:
    token, user = _creds()
    if not token or not user:
        log("  LINEのトークンかユーザーIDが未設定のため、送信をスキップしました")
        return
    http_json(PUSH_URL, method="POST",
              body={"to": user, "messages": [{"type": "text", "text": text[:5000]}]},
              headers={"Authorization": f"Bearer {token}"})
    log("  LINEに送信しました")


def coverage_text(c: dict) -> str:
    """「12媒体が報道（日本2・一般1）」のような表記。"""
    text = f"{c.get('n_outlets', 1)}媒体が報道"
    extra = []
    if c.get("jp_outlets"):
        extra.append(f"日本{len(c['jp_outlets'])}")
    if c.get("mainstream_outlets"):
        extra.append(f"一般{len(c['mainstream_outlets'])}")
    return text + (f"（{'・'.join(extra)}）" if extra else "")


def build_digest(day: dict, page_url: str) -> str:
    d = date.fromisoformat(day["date"])
    pick = day["picks"]
    stories = {c["id"]: c for c in day["candidates"]}
    lines = [f"■ 今日の洋楽ネタ（{d.month}/{d.day} {WEEKDAYS[d.weekday()]}）", ""]
    for n, t in enumerate(pick["top"], start=1):
        c = stories.get(t["id"], {})
        genre = t.get("genre") or c.get("genre", "")
        meta = ["その他" if genre in ("Other", "General", "") else genre, coverage_text(c)]
        v = c.get("video")
        if v and v.get("views"):
            meta.append(f"関連動画 {fmt_views(v['views'])}")
        lines.append(f"【{n}】{t['title_ja']}")
        lines.append("｜".join(m for m in meta if m))
        lines.append(f"要点：{t.get('line_point', '')}")
        if t.get("reels"):
            lines.append(f"リール案：{t['reels'][0].get('hook', '')}")
        lines.append("")
    tours = pick.get("japan_tours", [])
    if tours:
        lines.append("― 来日情報 ―")
        lines.extend(f"・{t['title_ja']}" for t in tours[:8])
        if len(tours) > 8:
            lines.append(f"ほか{len(tours) - 8}件（サイトに掲載）")
        lines.append("")
    runner_lines = []
    for g, items in pick["runners_up"].items():
        if g == "Other":  # いつでも使えるネタはサイトだけに載せる
            continue
        for r in items:
            runner_lines.append(f"{g}：{r['title_ja']}")
    if runner_lines:
        lines.append("― ジャンル別の次点 ―")
        lines.extend(runner_lines)
        lines.append("")
    lines.append("▶ 詳細・元記事・過去分")
    lines.append(page_url)
    return "\n".join(lines)


def send_error(message: str, run_url: str = "") -> None:
    text = f"■ 洋楽ネタまとめ：今日の作成に失敗しました\n\n{message[:600]}"
    if run_url:
        text += f"\n\n詳細（GitHubの実行ログ）：\n{run_url}"
    try:
        push_text(text)
    except Exception as e:  # noqa: BLE001
        log(f"  エラー通知の送信にも失敗しました: {e}")
