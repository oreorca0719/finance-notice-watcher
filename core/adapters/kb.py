"""KB국민은행 공지사항 어댑터 (omoney.kbstar.com / quics 게시판).

게시판 특성:
  · 목록·상세 모두 서버 렌더링 HTML. 로그인·JS 불필요
  · 게시글 본문(view_cont)은 항상 비어 있고 실제 공고 내용은 첨부 PDF 에 있음
  · 첨부는 GET 이 아니라 _FILE_NAME 등을 담은 POST 폼으로만 받을 수 있음
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_ROW = re.compile(
    r'<tr>\s*<td class="num">(?P<seq>\d+)</td>.*?articleId=(?P<aid>\d+).*?'
    r'>(?P<title>.*?)</a>.*?<td class="date">(?P<d>[\d.]+)</td>'
    r'\s*<td class="count">(?P<hits>[\d,]+)</td>',
    re.S,
)
_LOOSE = re.compile(
    r'articleId=(?P<aid>\d+)[^>]*>(?P<title>[^<]{4,200})</a>'
    r'(?P<tail>.{0,400}?)(?P<d>\d{4}\.\d{2}\.\d{2})',
    re.S,
)
_VIEW_DT = re.compile(
    r'<dl class="board_view">.*?<em>등록일</em>(?P<d>[\d.]+).*?<em>조회수</em>(?P<hits>[\d,]+).*?'
    r'<strong>(?P<title>.*?)</strong>',
    re.S,
)
_UPFILE_LI = re.compile(r'<li>\s*<form name="frmDownload\d+.*?</form>', re.S)
_F_NAME = re.compile(r'name="_FILE_NAME"\s+id="_FILE_NAME"\s+value="(?P<v>[^"]*)"')
_F_SITE = re.compile(r'name="_SITE_CODE"\s+id="_SITE_CODE"\s+value="(?P<v>[^"]*)"')
_F_DOM = re.compile(r'name="_DOMAIN_CODE"\s+id="_DOMAIN_CODE"\s+value="(?P<v>[^"]*)"')
_F_SEC = re.compile(r'name="_SECURITY_CODE"\s+id="_SECURITY_CODE"\s+value="(?P<v>[^"]*)"')
_F_FIX = re.compile(r'name="_FIXED_CODE"\s+id="_FIXED_CODE"\s+value="(?P<v>[^"]*)"')
_F_ACT = re.compile(r'<form name="frmDownload\d+"[^>]*action="(?P<v>[^"]*)"')
_ANCHOR = re.compile(r'<a[^>]*>(?P<v>.*?)</a>', re.S)
_TOTAL = re.compile(r'전체건수\s*:\s*([\d,]+)\s*건')


def _clean(raw: str) -> str:
    raw = re.sub(r'(?is)<br\s*/?>', '\n', raw)
    raw = re.sub(r'(?is)</(p|div|li|tr|h\d)>', '\n', raw)
    raw = html.unescape(re.sub(r'(?is)<[^>]+>', '', raw))
    lines = [re.sub(r'[ \t\xa0]+', ' ', ln).strip() for ln in raw.split('\n')]
    return '\n'.join(ln for ln in lines if ln)


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('.'))
    return date(y, m, d)


class KbAdapter(BaseAdapter):
    adapter_id = "kb"

    # ---------- URL ----------
    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return (
            f"{self.spec.base}/quics?page={self._p('page_code')}"
            f"&boardId={self._p('board_id')}&compId={self._p('comp_id')}"
            f"&bbsMode=list&viewPage={page}&searchCondition=title&searchStr="
        )

    def list_url_alt(self, page: int) -> Optional[str]:
        c = self._p('comp_id')
        return f"{self.spec.base}/quics?page={self._p('page_code')}&cc={c}:{c}&viewPage={page}"

    def detail_url(self, article_id: str) -> str:
        return (
            f"{self.spec.base}/quics?page={self._p('page_code')}"
            f"&boardId={self._p('board_id')}&compId={self._p('comp_id')}"
            f"&articleId={article_id}&bbsMode=view&viewPage=1"
            f"&articleClass=&searchCondition=title&searchStr="
        )

    def validate(self, html_text: str) -> bool:
        return 'articleId=' in html_text and ('tbl_list' in html_text or 'bbsMode' in html_text)

    # ---------- 파싱 ----------
    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group('aid')
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=aid,
                title=_clean(m.group('title')), posted_at=_to_date(m.group('d')),
                hits=int(m.group('hits').replace(',', '')), url=self.detail_url(aid),
            ))
        used = 'strict'

        if not notices:  # 구조 개편 대비 폴백
            used = 'loose'
            seen: set = set()
            for m in _LOOSE.finditer(html_text):
                aid = m.group('aid')
                if aid in seen:
                    continue
                seen.add(aid)
                title = _clean(m.group('title'))
                if not title:
                    continue
                notices.append(Notice(
                    seq=0, article_id=aid, title=title,
                    posted_at=_to_date(m.group('d')), url=self.detail_url(aid),
                ))

        tm = _TOTAL.search(_clean(html_text))
        return notices, (int(tm.group(1).replace(',', '')) if tm else None), used

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        dt = _VIEW_DT.search(html_text)
        if dt:
            notice.title = _clean(dt.group('title')) or notice.title
            notice.posted_at = _to_date(dt.group('d'))
            notice.hits = int(dt.group('hits').replace(',', ''))

        atts: List[Attachment] = []
        zone = html_text.split('class="upfile"', 1)
        if len(zone) > 1:
            for li in _UPFILE_LI.finditer(zone[1].split('</dd>', 1)[0]):
                block = li.group(0)
                fn = _F_NAME.search(block)
                if not fn:
                    continue
                a = _ANCHOR.search(block)
                disp = _clean(a.group('v')) if a else fn.group('v')
                g = lambda rx, dflt: (rx.search(block).group('v') if rx.search(block) else dflt)  # noqa: E731
                atts.append(Attachment(
                    display_name=html.unescape(disp) or html.unescape(fn.group('v')),
                    server_name=html.unescape(fn.group('v')),
                    action=g(_F_ACT, '/quics?asfilecode=534213'),
                    form={
                        "_DOMAIN_CODE": g(_F_DOM, 'bbs'),
                        "_SITE_CODE": g(_F_SITE, '21/648'),
                        "_SECURITY_CODE": g(_F_SEC, 'Y'),
                        "_FIXED_CODE": g(_F_FIX, 'Y'),
                        "_FILE_NAME": html.unescape(fn.group('v')),
                    },
                ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
