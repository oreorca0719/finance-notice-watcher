"""메일 본문 생성 계층.

메일 클라이언트(아웃룩·웹메일)는 <head> 의 <style> 블록을 제거하는 경우가 있어
모든 서식을 인라인 스타일로 넣고 레이아웃도 table 로 짠다. div+flex 는 쓰지 않는다.

본문에 담는 정보는 네 가지로 고정한다: 기관명 · 공고 제목 · 공고번호 · 등록일 (+ 원문 링크).
첨부 파일명은 넣지 않는다.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.models import Notice

# ── 색 ─────────────────────────────────────────────────────────────
INK = "#1b1f24"        # 본문
MUTED = "#6d7480"      # 보조
LINE = "#e4e7eb"       # 경계선
BG = "#f2f4f6"         # 바깥 배경
BRAND = "#123a5e"      # 헤더 남색
ACCENT = "#c8a349"     # 포인트 금색
CHIP_BG = "#eef2f6"
WARN_BG = "#fff8e6"
WARN_INK = "#7a5b00"
ALERT = "#b42318"

FONT = ("-apple-system,'Segoe UI','Malgun Gothic','맑은 고딕',"
        "'Apple SD Gothic Neo',sans-serif")


def _shell(title: str, subtitle: str, inner: str, footer: str,
           bar: str = BRAND) -> str:
    """공통 외곽. 600px 고정폭 — 메일 클라이언트 표준."""
    return f"""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="background:{BG};padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
 style="width:600px;max-width:100%;background:#ffffff;border:1px solid {LINE};
 border-radius:10px;overflow:hidden;font-family:{FONT};">

  <tr><td style="height:4px;background:{bar};font-size:0;line-height:0;">&nbsp;</td></tr>

  <tr><td style="padding:24px 28px 18px 28px;border-bottom:1px solid {LINE};">
    <div style="font-size:11px;letter-spacing:.14em;color:{ACCENT};font-weight:700;
     text-transform:uppercase;">PRON SOLUTION</div>
    <div style="font-size:19px;color:{INK};font-weight:700;margin-top:7px;
     line-height:1.35;">{html.escape(title)}</div>
    <div style="font-size:12.5px;color:{MUTED};margin-top:6px;">{html.escape(subtitle)}</div>
  </td></tr>

  {inner}

  <tr><td style="padding:16px 28px;background:#fafbfc;border-top:1px solid {LINE};
   font-size:11.5px;color:{MUTED};line-height:1.6;">{footer}</td></tr>
</table>
</td></tr></table></body></html>"""


def _group(notices: List[Notice]) -> "OrderedDict[str, List[Notice]]":
    out: "OrderedDict[str, List[Notice]]" = OrderedDict()
    ordered = sorted(notices, key=lambda x: (x.source_name, -x.posted_at.toordinal(), -x.seq))
    for n in ordered:
        out.setdefault(n.source_name, []).append(n)
    return out


def render_notices(
    notices: List[Notice],
    attached: Optional[Dict[str, set]] = None,   # 서명 호환용. 본문에는 쓰지 않는다
    failed_sources: Optional[List[str]] = None,
) -> Tuple[str, str, str]:
    """(제목, HTML본문, 텍스트본문)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grouped = _group(notices)
    blocks: List[str] = []
    lines: List[str] = []

    if failed_sources:
        blocks.append(
            f'<tr><td style="padding:11px 28px;background:{WARN_BG};'
            f'border-bottom:1px solid {LINE};font-size:12px;color:{WARN_INK};">'
            f'조회하지 못한 기관: {html.escape(", ".join(failed_sources))} — '
            f'해당 기관의 신규 공고는 포함되지 않았습니다.</td></tr>'
        )
        lines.append(f"[주의] 조회 실패 기관: {', '.join(failed_sources)}\n")

    for src, items in grouped.items():
        blocks.append(
            f'<tr><td style="padding:15px 28px 9px 28px;">'
            f'<span style="font-size:13.5px;font-weight:700;color:{BRAND};">{html.escape(src)}</span>'
            f'<span style="font-size:11.5px;color:{MUTED};margin-left:7px;">{len(items)}건</span>'
            f'</td></tr>'
        )
        lines.append(f"── {src} ({len(items)}건) ──")

        rows = []
        for n in items:
            meta = f"공고번호 {n.seq}" if n.seq else "공고번호 —"
            rows.append(
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
                f' border="0" style="margin:0 0 8px 0;background:#ffffff;'
                f'border:1px solid {LINE};border-radius:8px;">'
                f'<tr><td style="padding:14px 16px;">'
                f'<a href="{html.escape(n.url)}" style="font-size:14.5px;font-weight:600;'
                f'color:{INK};text-decoration:none;line-height:1.5;">{html.escape(n.title)}</a>'
                f'<div style="margin-top:9px;font-size:11.5px;color:{MUTED};">'
                f'<span style="background:{CHIP_BG};border-radius:4px;padding:3px 8px;">'
                f'{html.escape(meta)}</span>'
                f'<span style="margin-left:8px;">등록일 {n.posted_at.isoformat()}</span>'
                f'<a href="{html.escape(n.url)}" style="margin-left:8px;color:{BRAND};'
                f'text-decoration:none;font-weight:600;">원문 보기 &rsaquo;</a>'
                f'</div></td></tr></table>'
            )
            lines.append(f"  · {n.title}")
            lines.append(f"    {meta} | 등록일 {n.posted_at.isoformat()}")
            lines.append(f"    {n.url}")
        blocks.append(f'<tr><td style="padding:0 28px 6px 28px;">{"".join(rows)}</td></tr>')
        lines.append("")

    subject = f"신규 금융 프로젝트 공고 알림 {len(notices)}건"
    body_html = _shell(
        f"신규 금융 프로젝트 공고 {len(notices)}건",
        f"{len(grouped)}개 기관 · 확인 시각 {now}",
        "".join(blocks),
        "금융권 공고 게시판을 매일 자동 확인해 발송됩니다. "
        "이미 알린 공고는 다시 보내지 않습니다.",
    )
    return subject, body_html, "\n".join(lines)


def render_alert(reason: str, detail: str) -> Tuple[str, str, str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    inner = (
        f'<tr><td style="padding:18px 28px;">'
        f'<div style="font-size:14.5px;font-weight:700;color:{ALERT};">{html.escape(reason)}</div>'
        f'<div style="font-size:11.5px;color:{MUTED};margin-top:6px;">감지 시각 {now}</div>'
        f'<pre style="white-space:pre-wrap;word-break:break-word;background:#f6f7f9;'
        f'border:1px solid {LINE};border-radius:6px;padding:12px;font-size:11.5px;'
        f'color:#3f4550;margin:12px 0 0 0;">{html.escape(detail[:4000])}</pre>'
        f'</td></tr>'
    )
    return (
        "공고 감시기 장애 경보",
        _shell("공고 감시기 장애 경보", "자동 조회가 정상 동작하지 않았습니다. 수동 확인이 필요합니다.",
               inner,
               "감시기가 조용히 멈추는 것을 막기 위한 경보입니다. "
               "사이트 구조 변경이나 차단 도입이 원인일 수 있습니다.",
               bar=ALERT),
        f"{reason}\n\n{detail[:4000]}",
    )


def render_heartbeat(
    source_status: List[Tuple[str, bool, int, Optional[int]]]
) -> Tuple[str, str, str]:
    """source_status: [(기관명, 성공여부, 조회건수, 게시판전체건수), ...]"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok_all = all(ok for _, ok, _, _ in source_status)
    rows = []
    text = [f"감시기 정상 동작 중 ({now}). 신규 공고 없음.", ""]
    for name, ok, scanned, _total in source_status:
        mark = "정상" if ok else "실패"
        color = MUTED if ok else ALERT
        rows.append(
            f'<tr><td style="padding:8px 0;border-bottom:1px solid {LINE};font-size:12.5px;'
            f'color:{INK};">{html.escape(name)}'
            f'<span style="float:right;color:{color};font-size:11.5px;">'
            f'{mark} · 조회 {scanned}건</span></td></tr>'
        )
        text.append(f"  {name}: {mark} (조회 {scanned}건)")
    inner = (
        f'<tr><td style="padding:14px 28px 18px 28px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{"".join(rows)}</table></td></tr>'
    )
    return (
        "공고 감시기 정상 동작 보고" if ok_all else "공고 감시기 부분 실패 보고",
        _shell("감시기 생존 보고", f"확인 시각 {now} · 신규 공고 없음", inner,
               "주기적 생존 보고입니다. config.yaml 의 send_when_empty 로 끌 수 있습니다.",
               bar=BRAND if ok_all else ALERT),
        "\n".join(text),
    )
