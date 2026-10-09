"""閲覧サイト（GitHub Pages）を作る。data/ に貯まった毎日の結果から、静的なHTMLを生成します。"""
import json
import re
from datetime import date, datetime
from html import escape
from pathlib import Path

from notify import coverage_text
from util import JST, fmt_views

WEEKDAYS = "月火水木金土日"
GENRE_CLASS = {"Dance": "g-dance", "POP": "g-pop", "Hip-Hop": "g-hiphop", "来日": "g-visit"}

FONTS = ("https://fonts.googleapis.com/css2?family=Dela+Gothic+One"
         "&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap")

CSS = r"""
:root{
  --paper:#F6F7F4; --ink:#1D2026; --ink-soft:#5A606B; --rule:#D9DCD6; --well:#ECEEE9;
  --blue:#0078BF; --pink:#FF48B0; --yellow:#FFD900; --other:#8A8F98;
  --on-blue:#FFFFFF; --on-pink:#1D2026; --on-yellow:#1D2026; --on-other:#FFFFFF;
  --blend:multiply;
  --sans:"Zen Kaku Gothic New","Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Yu Gothic",sans-serif;
  --display:"Dela Gothic One","Hiragino Sans","Noto Sans JP",sans-serif;
  color-scheme:light dark;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#171A21; --ink:#ECEDEA; --ink-soft:#A3A8B2; --rule:#2E333D; --well:#1F232B;
    --blue:#3C9BE6; --pink:#FF5EBB; --yellow:#FFE14D; --other:#7D838D;
    --on-blue:#0E1116; --on-pink:#0E1116; --on-yellow:#0E1116; --on-other:#0E1116;
    --blend:screen;
  }
}
*{box-sizing:border-box}
[hidden]{display:none !important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 16px/1.8 var(--sans);
  padding:env(safe-area-inset-top,0) 0 env(safe-area-inset-bottom,0)}
a{color:inherit;text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{text-decoration-thickness:2px}
:focus-visible{outline:3px solid var(--blue);outline-offset:2px;border-radius:2px}
.wrap{max-width:680px;margin:0 auto;padding:28px 20px 64px}
.topbar{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  font-size:14px;color:var(--ink-soft)}
.topbar nav{display:flex;gap:16px}
.masthead{margin:10px 0 8px;font:400 clamp(30px,8vw,42px)/1.2 var(--display);letter-spacing:.02em}
.overview{margin:0 0 36px;color:var(--ink-soft)}

.g-dance{--g:var(--blue);--on-g:var(--on-blue);--g2:var(--pink)}
.g-pop{--g:var(--pink);--on-g:var(--on-pink);--g2:var(--blue)}
.g-hiphop{--g:var(--yellow);--on-g:var(--on-yellow);--g2:var(--pink)}
.g-other{--g:var(--other);--on-g:var(--on-other);--g2:var(--blue)}
.g-visit{--g:var(--ink);--on-g:var(--paper);--g2:var(--pink)}

.chip{display:inline-block;background:var(--g);color:var(--on-g);font-size:12px;font-weight:700;
  line-height:1;padding:5px 8px 4px;border-radius:3px;white-space:nowrap}

.pick{border-top:4px solid var(--g);padding:22px 0 30px}
.pick-head{display:grid;grid-template-columns:auto 1fr;gap:4px 18px;align-items:start}
.num{position:relative;display:block;font:400 76px/0.9 var(--display);color:var(--g);
  min-width:.75em;isolation:isolate}
.num::before{content:attr(data-n);position:absolute;left:5px;top:4px;color:var(--g2);
  mix-blend-mode:var(--blend);z-index:-1;opacity:.85}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font-size:13px;color:var(--ink-soft)}
.pick h2{margin:6px 0 0;font-size:21px;line-height:1.5;font-weight:700}
.summary{margin:16px 0 0}
.why{margin:10px 0 0;color:var(--ink-soft)}
.label{font-weight:700;color:var(--ink);margin-right:.5em}
.reels{margin:20px 0 0;padding:14px 16px;background:var(--well);border-radius:6px}
.reels h3{margin:0 0 6px;font-size:13px;font-weight:700;color:var(--ink-soft)}
.reels ul{list-style:none;margin:0;padding:0}
.reels li+li{margin-top:12px;padding-top:12px;border-top:1px dashed var(--rule)}
.hook{margin:0;font-size:17px;font-weight:700;line-height:1.6}
.angle{margin:2px 0 0;font-size:14px;color:var(--ink-soft)}
.sources{margin:16px 0 0;font-size:14px;color:var(--ink-soft)}
.sources a{margin-right:10px;white-space:nowrap}

h2.section{margin:44px 0 6px;font-size:18px;font-weight:700}
.runner-group{margin-top:18px}
.runner-group ul{list-style:none;margin:8px 0 0;padding:0 0 0 14px;border-left:3px solid var(--g)}
.runner-group li+li{margin-top:12px}
.runner-title{font-weight:700}
.runner-note{display:block;font-size:14px;color:var(--ink-soft)}
.empty{color:var(--ink-soft);font-size:14px}

details{margin-top:28px;border-top:1px solid var(--rule);padding-top:14px}
summary{cursor:pointer;font-weight:700;font-size:15px}
.scroll{overflow-x:auto;margin-top:12px}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:520px}
th,td{text-align:left;padding:8px 10px 8px 0;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:12px;color:var(--ink-soft);font-weight:700}
td.n{text-align:right;white-space:nowrap}
.health{list-style:none;padding:0;margin:12px 0 0;font-size:14px}
.health li{padding:4px 0;border-bottom:1px solid var(--rule)}
.ng{color:var(--pink);font-weight:700}
.compare-item{margin:12px 0 0}

.pager{display:flex;justify-content:space-between;gap:12px;margin-top:40px;font-size:14px}
footer{margin-top:40px;font-size:12px;color:var(--ink-soft);display:flex;flex-wrap:wrap;gap:4px 16px}

.filters{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 24px}
.filters button{font:700 14px/1 var(--sans);padding:9px 14px;border-radius:999px;cursor:pointer;
  border:2px solid var(--ink);background:transparent;color:var(--ink)}
.filters button[aria-pressed="true"]{background:var(--ink);color:var(--paper)}
.day{padding:16px 0;border-bottom:1px solid var(--rule)}
.day h2{margin:0 0 6px;font-size:16px}
.day ul{list-style:none;margin:0;padding:0}
.day li{display:flex;gap:10px;align-items:baseline;padding:3px 0}
.day li .chip{flex:none;min-width:64px;text-align:center}
"""

FILTER_JS = r"""
document.querySelectorAll('.filters button').forEach(function(b){
  b.addEventListener('click',function(){
    var g=b.dataset.genre;
    document.querySelectorAll('.filters button').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});
    document.querySelectorAll('.day').forEach(function(day){
      var any=false;
      day.querySelectorAll('li').forEach(function(li){
        var show=(g==='all'||li.dataset.genre===g); li.hidden=!show; if(show)any=true;
      });
      day.hidden=!any;
    });
  });
});
"""


def model_name(model_id: str) -> str:
    """claude-opus-5-5 → Opus 5.5 のように読みやすくする。"""
    m = re.match(r"claude-([a-z]+)-(\d+)-(\d+)", model_id or "")
    return f"{m.group(1).capitalize()} {m.group(2)}.{m.group(3)}" if m else (model_id or "")


def _glabel(genre: str) -> str:
    """ニュースのジャンル表示。「Other」は雑学ネタ枠と紛らわしいので「その他」と出す。"""
    return "その他" if genre in ("Other", "General", "") else genre


def _gclass(genre: str) -> str:
    return GENRE_CLASS.get(genre, "g-other")


def _jdate(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"


def _page(title: str, body: str, root: str, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{root}style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎧</text></svg>">
</head><body><div class="wrap">
{body}
</div>{f'<script>{script}</script>' if script else ''}</body></html>
"""


def _video_text(v: dict | None) -> str:
    if not v or not v.get("views"):
        return ""
    s = f"関連動画 {fmt_views(v['views'])}"
    if v.get("hours_since") and v["hours_since"] <= 72:
        s += f"（公開{v['hours_since']}時間）"
    return s


def _sources_html(c: dict) -> str:
    seen, links = set(), []
    for a in c.get("articles", []):
        if a["outlet"] in seen:
            continue
        seen.add(a["outlet"])
        links.append(f'<a href="{escape(a["link"])}" rel="noopener" target="_blank">{escape(a["outlet"])}</a>')
    parts = [f'<span class="label">元記事</span>{"".join(links)}']
    if c.get("video"):
        v = c["video"]
        parts.append(f'<br><span class="label">関連動画</span><a href="{escape(v["url"])}" rel="noopener" '
                     f'target="_blank">{escape(v["title"][:60])}</a>')
    return f'<p class="sources">{"".join(parts)}</p>'


def _pick_html(n: int, t: dict, c: dict) -> str:
    genre = t.get("genre") or c.get("genre", "Other")
    meta = [f'<span class="chip">{escape(_glabel(genre))}</span>', f'<span>{escape(coverage_text(c))}</span>']
    vt = _video_text(c.get("video"))
    if vt:
        meta.append(f"<span>{escape(vt)}</span>")
    hits = c.get("chart", [])
    jp_hits = [h for h in hits if h.get("region") == "JP"]
    for ch in (jp_hits or hits)[:1]:
        meta.append(f"<span>YouTube音楽チャート {escape(ch['region'])} {ch['rank']}位</span>")
    reels = "".join(
        f'<li><p class="hook">「{escape(r.get("hook", ""))}」</p><p class="angle">{escape(r.get("angle", ""))}</p></li>'
        for r in t.get("reels", []))
    return f"""<article class="pick {_gclass(genre)}">
<div class="pick-head"><span class="num" data-n="{n}" aria-label="{n}位">{n}</span>
<div><div class="meta">{''.join(meta)}</div><h2>{escape(t.get('title_ja', ''))}</h2></div></div>
<p class="summary">{escape(t.get('summary_ja', ''))}</p>
<p class="why"><span class="label">なぜ話題か</span>{escape(t.get('why_ja', ''))}</p>
<div class="reels"><h3>リール案</h3><ul>{reels}</ul></div>
{_sources_html(c)}
</article>"""


def _runners_html(runners: dict, stories: dict) -> str:
    groups = []
    for g in ("Dance", "POP", "Hip-Hop", "Other"):
        if g == "Other" and "Other" not in runners:
            continue  # 機能追加前の日のまとめ
        items = runners.get(g, [])
        if items:
            lis = []
            for r in items:
                c = stories.get(r["id"], {})
                first = c.get("articles", [{}])[0]
                n_more = c.get("n_outlets", 1) - 1
                text = first.get("outlet", "") + (f"ほか{n_more}媒体" if n_more > 0 else "")
                link = (f' <a href="{escape(first["link"])}" rel="noopener" target="_blank">'
                        f'{escape(text)}</a>') if first.get("link") else ""
                lis.append(f'<li><span class="runner-title">{escape(r["title_ja"])}</span>'
                           f'<span class="runner-note">{escape(r.get("note", ""))}{link}</span></li>')
            body = f"<ul>{''.join(lis)}</ul>"
        else:
            body = '<p class="empty">今日は該当なし</p>'
        label = "Other（いつでも使えるネタ）" if g == "Other" else g
        groups.append(f'<div class="runner-group {_gclass(g)}"><span class="chip">{label}</span>{body}</div>')
    return '<h2 class="section">ジャンル別の次点</h2>' + "".join(groups)


def _tours_html(tours: list[dict], stories: dict) -> str:
    if not tours:
        return ""
    lis = []
    for t in tours:
        c = stories.get(t["id"], {})
        first = (c.get("articles") or [{}])[0]
        link = (f' <a href="{escape(first["link"])}" rel="noopener" target="_blank">{escape(first.get("outlet", ""))}</a>'
                if first.get("link") else "")
        note = escape(t.get("note", ""))
        lis.append(f'<li><span class="runner-title">{escape(t["title_ja"])}</span>'
                   f'<span class="runner-note">{note}{link}</span></li>')
    return (f'<h2 class="section">来日情報</h2><div class="runner-group g-visit">'
            f'<span class="chip">{len(tours)}件</span><ul>{"".join(lis)}</ul></div>')


def _compare_html(cmp: dict, stories: dict) -> str:
    items = []
    for n, t in enumerate(cmp.get("top", []), start=1):
        hook = t.get("reels", [{}])[0].get("hook", "") if t.get("reels") else ""
        items.append(f'<div class="compare-item"><b>{n}. {escape(t.get("title_ja", ""))}</b>'
                     f'<br><span class="angle">リール案：「{escape(hook)}」</span></div>')
    runners = [f'{g}：{escape(r["title_ja"])}' for g, rs in cmp.get("runners_up", {}).items() for r in rs]
    if runners:
        items.append(f'<p class="angle">次点　{"／".join(runners)}</p>')
    if cmp.get("japan_tours"):
        items.append(f'<p class="angle">来日情報　{len(cmp["japan_tours"])}件：'
                     f'{"／".join(escape(t["title_ja"]) for t in cmp["japan_tours"])}</p>')
    return (f'<details><summary>比較：{escape(model_name(cmp["model"]))} の選定</summary>'
            f'<p class="angle">{escape(cmp.get("overview", ""))}</p>{"".join(items)}</details>')


def _candidates_html(cands: list[dict]) -> str:
    rows = []
    for c in sorted(cands, key=lambda x: (-x["n_outlets"], -((x.get("video") or {}).get("views") or 0))):
        first = c["articles"][0]
        v = c.get("video")
        rows.append(f'<tr><td><a href="{escape(first["link"])}" rel="noopener" target="_blank">'
                    f'{escape(c["label"])}</a></td><td><span class="{_gclass(c["genre"])}">'
                    f'<span class="chip">{escape(_glabel(c["genre"]))}</span></span></td>'
                    f'<td class="n">{c["n_outlets"]}</td><td class="n">{escape(fmt_views(v["views"]) if v and v.get("views") else "—")}</td></tr>')
    return (f'<details><summary>今日の話題一覧（{len(cands)}件）</summary><div class="scroll"><table>'
            f'<thead><tr><th>話題</th><th>ジャンル</th><th>媒体数</th><th>動画再生数</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>')


def _health_html(health: list[dict]) -> str:
    ok = sum(1 for h in health if h["status"] == "ok")
    lis = []
    for h in health:
        state = "取得" if h["status"] == "ok" else '<span class="ng">取得できず</span>'
        grp = h.get("group", "海外音楽")
        tag = f"［{escape(grp)}］" if grp != "海外音楽" else ""
        lis.append(f'<li>{tag}{escape(h["name"])}：{state}（{escape(h["method"])}、新着{h["kept"]}件）</li>')
    return (f'<details><summary>記事の取得状況（{ok}/{len(health)}媒体）</summary>'
            f'<ul class="health">{"".join(lis)}</ul></details>')


def _footer(day: dict) -> str:
    gen = datetime.fromisoformat(day["generated_at"]).astimezone(JST)
    cost = day.get("cost", {})
    parts = [f"作成 {gen.month}/{gen.day} {gen:%H:%M}", f"選定 {escape(model_name(day['picks']['model']))}"]
    if cost.get("usd") is not None:
        parts.append(f"AI費用 約${cost['usd']:.2f}（約{round(cost['usd'] * cost.get('jpy_per_usd', 150))}円）")
    parts.append(f"記事 {day.get('article_count', 0)}本")
    return "<footer>" + "".join(f"<span>{p}</span>" for p in parts) + "</footer>"


def _day_body(day: dict, root: str, prev_d: str | None, next_d: str | None) -> str:
    stories = {c["id"]: c for c in day["candidates"]}
    picks = day["picks"]
    tops = "".join(_pick_html(n, t, stories.get(t["id"], {})) for n, t in enumerate(picks["top"], start=1))
    nav_prev = f'<a href="{root}days/{prev_d}.html">前の日</a>' if prev_d else "<span></span>"
    nav_next = f'<a href="{root}days/{next_d}.html">次の日</a>' if next_d else "<span></span>"
    extra = _candidates_html(day["candidates"])
    if day.get("compare"):
        extra = _compare_html(day["compare"], stories) + extra
    return f"""<div class="topbar"><span>{_jdate(day['date'])}</span>
<nav><a href="{root}index.html">最新</a><a href="{root}archive.html">過去のまとめ</a></nav></div>
<h1 class="masthead">今日の洋楽ネタ</h1>
<p class="overview">{escape(picks.get('overview', ''))}</p>
{tops}
{_tours_html(picks.get('japan_tours', []), stories)}
{_runners_html(picks['runners_up'], stories)}
{extra}
{_health_html(day.get('health', []))}
<div class="pager">{nav_prev}{nav_next}</div>
{_footer(day)}"""


def _archive_body(days: list[dict]) -> str:
    blocks = []
    for day in days:
        stories = {c["id"]: c for c in day["candidates"]}
        lis = []
        for t in day["picks"]["top"]:
            g = t.get("genre") or stories.get(t["id"], {}).get("genre", "Other")
            lis.append(f'<li data-genre="{escape(_glabel(g))}"><span class="{_gclass(g)}"><span class="chip">{escape(_glabel(g))}</span></span>'
                       f'<span>{escape(t["title_ja"])}</span></li>')
        for t in day["picks"].get("japan_tours", []):
            lis.append(f'<li data-genre="来日"><span class="g-visit"><span class="chip">来日</span></span>'
                       f'<span>{escape(t["title_ja"])}</span></li>')
        for g, rs in day["picks"]["runners_up"].items():
            for r in rs:
                lis.append(f'<li data-genre="{escape(g)}"><span class="{_gclass(g)}"><span class="chip">{escape(g)}</span></span>'
                           f'<span class="angle">次点：{escape(r["title_ja"])}</span></li>')
        blocks.append(f'<section class="day"><h2><a href="days/{day["date"]}.html">{_jdate(day["date"])}</a></h2>'
                      f'<ul>{"".join(lis)}</ul></section>')
    buttons = "".join(
        f'<button type="button" data-genre="{g}" aria-pressed="{"true" if g == "all" else "false"}">{label}</button>'
        for g, label in (("all", "すべて"), ("Dance", "Dance"), ("POP", "POP"), ("Hip-Hop", "Hip-Hop"), ("来日", "来日"), ("Other", "Other")))
    return f"""<div class="topbar"><span>{len(days)}日分</span><nav><a href="index.html">最新</a></nav></div>
<h1 class="masthead">過去のまとめ</h1>
<div class="filters" role="group" aria-label="ジャンルで絞り込む">{buttons}</div>
{''.join(blocks) or '<p class="empty">まだまとめがありません。</p>'}"""


EMPTY_BODY = """<h1 class="masthead">今日の洋楽ネタ</h1>
<p class="overview">まだまとめがありません。GitHubの「Actions」タブで「Run workflow」を押すと、最初のまとめが作られます。</p>"""


def build_site(data_dir: Path, out_dir: Path, site_title: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "days").mkdir(exist_ok=True)
    (out_dir / "style.css").write_text(CSS, encoding="utf-8")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")

    days = []
    for p in sorted(data_dir.glob("????-??-??.json"), reverse=True):
        try:
            days.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue

    if not days:
        (out_dir / "index.html").write_text(_page(site_title, EMPTY_BODY, ""), encoding="utf-8")
        (out_dir / "archive.html").write_text(_page(site_title, _archive_body([]), ""), encoding="utf-8")
        return

    for i, day in enumerate(days):
        prev_d = days[i + 1]["date"] if i + 1 < len(days) else None
        next_d = days[i - 1]["date"] if i > 0 else None
        title = f"{_jdate(day['date'])}の洋楽ネタ｜{site_title}"
        (out_dir / "days" / f"{day['date']}.html").write_text(
            _page(title, _day_body(day, "../", prev_d, next_d), "../"), encoding="utf-8")
        if i == 0:
            (out_dir / "index.html").write_text(
                _page(site_title, _day_body(day, "", prev_d, None), ""), encoding="utf-8")
    (out_dir / "archive.html").write_text(
        _page(f"過去のまとめ｜{site_title}", _archive_body(days), "", FILTER_JS), encoding="utf-8")
