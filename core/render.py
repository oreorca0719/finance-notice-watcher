"""메일 본문 생성 계층.

신규 공고 알림은 기관별 섹션으로 묶어 한 통에 담는다.
받는 사람이 아침에 메일 하나만 확인하면 되도록 하는 것이 목적이다.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.models import Notice

_STYLE = """
body{margin:0;padding:0;background:#f4f5f7;
font-family:-apple-system,'Segoe UI','Malgun Gothic','맑은 고딕',sans-serif;color:#1f2328}
.wrap{max-width:680px;margin:0 auto;padding:24px 16px 40px}
.card{background:#fff;border:1px solid #e3e5e8;border-radius:10px;overflow:hidden}
.head{padding:18px 22px;border-bottom:1px solid #eceef0;background:#fffdf3}
.head h1{margin:0;font-size:17px;letter-spacing:-.2px}
.head p{margin:5px 0 0;font-size:12.5px;color:#6b7280}
.src{padding:11px 22px;background:#f7f8fa;border-bottom:1px solid #eceef0;
border-top:1px solid #eceef0;font-size:13px;font-weight:700;color:#374151}
.src span{font-weight:400;color:#8b9099;font-size:12px}
.item{padding:16px 22px;border-bottom:1px solid #f0f1f3}
.t{font-size:15px;font-weight:600;margin:0 0 6px;line-height:1.45}
.t a{color:#1a1d21;text-decoration:none}
.m{font-size:12px;color:#6b7280;margin:0}
.files{margin:9px 0 0;padding:0;list-style:none}
.files li{display:inline-block;background:#f5f2e3;color:#5b5320;border-radius:4px;
padding:3px 8px;margin:3px 5px 0 0;font-size:11.5px}
.btn{display:inline-block;margin-top:10px;padding:7px 13px;background:#1f2328;color:#fff;
border-radius:6px;font-size:12.5px;text-decoration:none}
.foot{padding:14px 22px;font-size:11.5px;color:#8b9099;background:#fafbfc}
.warn{padding:12px 22px;background:#fff8e6;border-bottom:1px solid #f3e6c0;
font-size:12px;color:#7a5b00}
.alert .head{background:#fff5f5}
.alert h1{color:#b42318}
pre.err{white-space:pre-wrap;word-break:break-word;background:#f6f7f9;border-radius:6px;
padding:12px;font-size:11.5px;color:#3f4550;margin:10px 0 0}
"""


def _shell(title: str, subtitle: str, inner: str, footer: str, alert: bool = False) -> str:
    cls = "card alert" if alert else "card"
    return (
        f'<html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body><div class="wrap">'
        f'<div class="{cls}"><div class="head"><h1>{html.escape(title)}</h1>'
        f'<p>{html.escape(subtitle)}</p></div>{inner}'
        f'<div class="foot">{footer}</div></div></div></body></html>'
    )


def _group(notices: List[Notice]) -> "OrderedDict[str, List[Notice]]":
    out: "OrderedDict[str, List[Notice]]" = OrderedDict()
    for n in sorted(notices, key=lambda x: (x.source_name, x.posted_at, x.seq)):
        out.setdefault(n.source_name, []).append(n)
    return out


def render_notices(
    notices: List[Notice],
    attached: Optional[Dict[str, set]] = None,
    failed_sources: Optional[List[str]] = None,
) -> Tuple[str, str, str]:
    """(제목, HTML본문, 텍스트본문). 기관별 섹션으로 묶는다."""
    attached = attached or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grouped = _group(notices)

    blocks: List[str] = []
    text_lines: List[str] = []

    if failed_sources:
        blocks.append(
            '<div class="warn">조회하지 못한 기관이 있습니다: '
            + html.escape(", ".join(failed_sources))
            + ' — 해당 기관의 신규 공고는 이 메일에 포함되지 않았습니다.</div>'
        )
        text_lines.append(f"[주의] 조회 실패 기관: {', '.join(failed_sources)}\n")

    for src, items in grouped.items():
        blocks.append(f'<div class="src">{html.escape(src)} <span>{len(items)}건</span></div>')
        text_lines.append(f"── {src} ({len(items)}건) ──")
        for n in items:
            files = ""
            if n.attachments:
                lis = "".join(
                    f"<li>{html.escape(a.display_name)}"
                    f"{' · 첨부됨' if a.display_name in attached.get(n.key, set()) else ''}</li>"
                    for a in n.attachments
                )
                files = f'<ul class="files">{lis}</ul>'
            blocks.append(
                f'<div class="item"><p class="t">'
                f'<a href="{html.escape(n.url)}">{html.escape(n.title)}</a></p>'
                f'<p class="m">공고번호 {n.seq or "-"} · 등록일 {n.posted_at.isoformat()}</p>'
                f'{files}<a class="btn" href="{html.escape(n.url)}">원문 보기</a></div>'
            )
            text_lines.append(f"  [{n.seq or '-'}] {n.title}")
            text_lines.append(f"     등록일 {n.posted_at.isoformat()}")
            text_lines.append(f"     {n.url}")
            for a in n.attachments:
                text_lines.append(f"     - 첨부: {a.display_name}")
        text_lines.append("")

    n_src = len(grouped)
    if len(notices) == 1:
        subject = f"신규 공고 1건 · {notices[0].source_name} {notices[0].title[:32]}"
    elif n_src == 1:
        subject = f"{next(iter(grouped))} 신규 공고 {len(notices)}건"
    else:
        subject = f"신규 공고 {len(notices)}건 · {n_src}개 기관"

    body_html = _shell(
        f"신규 프로젝트 공고 {len(notices)}건",
        f"확인 시각 {now} · {n_src}개 기관",
        "".join(blocks),
        "금융권 공고 게시판을 자동 확인해 발송되었습니다. "
        "이미 알린 공고는 다시 보내지 않습니다.",
    )
    return subject, body_html, "\n".join(text_lines)


def render_alert(reason: str, detail: str) -> Tuple[str, str, str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    inner = (
        f'<div class="item"><p class="t">{html.escape(reason)}</p>'
        f'<p class="m">감지 시각 {now}</p>'
        f'<pre class="err">{html.escape(detail[:4000])}</pre></div>'
    )
    body = _shell(
        "공고 감시기 장애 경보",
        "자동 조회가 정상 동작하지 않았습니다. 수동 확인이 필요합니다.",
        inner,
        "이 경보는 감시기가 조용히 멈추는 것을 막기 위해 발송됩니다. "
        "사이트 구조 변경·차단 도입이 원인일 수 있습니다.",
        alert=True,
    )
    return "⚠ 공고 감시기 장애 경보", body, f"{reason}\n\n{detail[:4000]}"


def render_heartbeat(source_status: List[Tuple[str, bool, int, Optional[int]]]) -> Tuple[str, str, str]:
    """source_status: [(기관명, 성공여부, 조회건수, 게시판전체건수), ...]"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    text = [f"감시기 정상 동작 중 ({now}). 신규 공고 없음.", ""]
    for name, ok, scanned, total in source_status:
        mark = "정상" if ok else "실패"
        rows.append(
            f'<div class="item"><p class="t">{html.escape(name)} — {mark}</p>'
            f'<p class="m">조회 {scanned}건 · 게시판 전체 {total or "-"}건</p></div>'
        )
        text.append(f"  {name}: {mark} (조회 {scanned}건)")
    ok_all = all(ok for _, ok, _, _ in source_status)
    return (
        "공고 감시기 정상 동작 보고" if ok_all else "공고 감시기 부분 실패 보고",
        _shell("공고 감시기 생존 보고", f"확인 시각 {now} · 신규 공고 없음",
               "".join(rows),
               "주기적 생존 보고입니다. config.yaml 의 send_when_empty 로 끌 수 있습니다."),
        "\n".join(text),
    )
