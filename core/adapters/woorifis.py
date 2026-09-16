"""우리에프아이에스 사업공고 어댑터 (www.woorifis.com).

목록은 표이고 상세 링크는 /kor/company/announceDetail?boardId=번호 이다.
구분 칸(공고/재공고)이 있어 제목 칸(td.alignLeft)만 집는다.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

_ROW = re.compile(
    r'<tr>\s*<td>(?P<seq>\d+)</td>.*?'
    r'<a href="(?P<href>[^"]*announceDetail\?boardId=(?P<aid>\d+))"[^>]*>(?P<title>.*?)</a>'
    r'.*?<td>\s*(?P<d>\d{4}\.\d{2}\.\d{2})\s*</td>',
    re.S,
)
_LOOSE = re.compile(
    r'announceDetail\?boardId=(?P<aid>\d+)"[^>]*>(?P<title>[^<]{4,200})</a>.{0,400}?(?P<d>\d{4}\.\d{2}\.\d{2})',
    re.S,
)


def _clean(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split('.'))
    return date(y, m, d)


class WooriFisAdapter(BaseAdapter):
    adapter_id = "woorifis"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._p('list_path')}?pageIndex={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._p('list_path')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}{self._p('view_path')}{article_id}"

    def validate(self, html_text: str) -> bool:
        return "announceDetail?boardId=" in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            title = _clean(m.group('title'))
            if not title:
                continue
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=m.group('aid'), title=title,
                posted_at=_to_date(m.group('d')),
                url=self.spec.base + html.unescape(m.group('href')),
            ))
        used = "strict"

        if not notices:
            used = "loose"
            seen: set = set()
            for m in _LOOSE.finditer(html_text):
                aid = m.group('aid')
                if aid in seen:
                    continue
                seen.add(aid)
                notices.append(Notice(seq=0, article_id=aid, title=_clean(m.group('title')),
                                      posted_at=_to_date(m.group('d')), url=self.detail_url(aid)))
        return notices, (max((n.seq for n in notices), default=None) or None), used
