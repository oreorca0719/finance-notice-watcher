"""농협정보시스템 입찰공고 어댑터 (www.nonghyupit.com).

NH 그룹의 IT 자회사라 전산센터 설비·시스템 구축 발주가 집중된다.

게시판 특성:
  · 목록 행: td.td-num / td.td-subject > a[href=/bbs/{site}/{menu}/{artcl}/artclView.do]
             / td.td-ing(진행상태) / td.td-date / td.td-access / td.td-file
  · 상세 URL 이 목록 링크에 그대로 들어 있어 조립이 단순하다
  · 페이징은 ?page=N
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_ROW = re.compile(
    r'<td class="td-num">\s*(?P<seq>\d+)\s*</td>\s*'
    r'<td class="td-subject">\s*<a\s+href="(?P<href>[^"]*?/(?P<aid>\d+)/artclView\.do)"[^>]*>'
    r'(?P<title>.*?)</a>.*?'
    r'<td class="td-date">\s*(?P<d>\d{4}\.\d{2}\.\d{2})\s*</td>',
    re.S,
)
_LOOSE = re.compile(
    r'/(?P<aid>\d+)/artclView\.do"[^>]*>(?P<title>.{4,300}?)</a>'
    r'.{0,500}?(?P<d>\d{4}\.\d{2}\.\d{2})',
    re.S,
)
_STATE = re.compile(r'<td class="td-ing"><span[^>]*>(?P<v>[^<]*)</span>')
_FILE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*(?:/bbs/[^"]*/download\.do|fileDown|/download)[^"]*)"[^>]*>'
    r'(?P<name>.*?)</a>',
    re.S,
)


def _one(raw: str) -> str:
    raw = re.sub(r'(?is)<[^>]+>', ' ', raw)
    return re.sub(r'\s+', ' ', html.unescape(raw)).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('.'))
    return date(y, m, d)


class NhItAdapter(BaseAdapter):
    adapter_id = "nhit"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._p('list_path')}?page={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._p('list_path')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return (f"{self.spec.base}/bbs/{self._p('site_key', 'nonghyupit')}"
                f"/{self._p('menu_key', '675')}/{article_id}/artclView.do")

    def validate(self, html_text: str) -> bool:
        return 'artclView.do' in html_text or 'td-subject' in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group('aid')
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=aid,
                title=_one(m.group('title')), posted_at=_to_date(m.group('d')),
                url=self.spec.base + html.unescape(m.group('href')),
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
                title = _one(m.group('title'))
                if title:
                    notices.append(Notice(
                        seq=0, article_id=aid, title=title,
                        posted_at=_to_date(m.group('d')), url=self.detail_url(aid),
                    ))

        total = max((n.seq for n in notices), default=None) or None
        return notices, total, used

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        atts: List[Attachment] = []
        seen: set = set()
        for m in _FILE.finditer(html_text):
            href = html.unescape(m.group('href'))
            if href in seen or href.startswith(('#', 'javascript')):
                continue
            seen.add(href)
            name = _one(m.group('name'))
            if not name:
                continue
            atts.append(Attachment(
                display_name=name,
                url=href if href.startswith('http') else self.spec.base + href,
                referer=notice.url,
            ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
