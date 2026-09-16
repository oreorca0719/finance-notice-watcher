"""한국증권금융 게시판 어댑터 (www.ksfc.co.kr:4443).

공지사항과 보도자료가 같은 마크업을 쓴다. 차이는 params 로 가른다.
  · 상세는 목록에서 javascript:goView('번호') 로 열지만, view.do?ntatSno=번호 GET 으로도 열린다(실측).
  · 공지사항 목록에는 구분 칸(채용·안내 등)이 하나 더 있어 정규식을 느슨하게 둔다.
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
    r'class="bbsTitle"><a href="javascript:goView\(\'(?P<aid>\d+)\'\);"\s*title="(?P<title>[^"]*)"'
    r'.*?(?P<d>\d{4}\.\d{2}\.\d{2})',
    re.S,
)
_LOOSE = re.compile(
    r"goView\('(?P<aid>\d+)'\).*?title=\"(?P<title>[^\"]{4,200})\".*?(?P<d>\d{4}\.\d{2}\.\d{2})",
    re.S,
)


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('.'))
    return date(y, m, d)


class KsfcAdapter(BaseAdapter):
    adapter_id = "ksfc"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        path = self._p("list_path")
        sep = "&" if "?" in path else "?"
        return f"{self.spec.base}{path}{sep}pg={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._p('list_path')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}{self._p('view_path')}?ntatSno={article_id}"

    def validate(self, html_text: str) -> bool:
        return "goView(" in html_text or "bbsTitle" in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group("aid")
            notices.append(Notice(
                seq=int(m.group("seq")), article_id=aid,
                title=html.unescape(m.group("title")).strip(),
                posted_at=_to_date(m.group("d")), url=self.detail_url(aid),
            ))
        used = "strict"

        if not notices:  # 구조 개편 대비 폴백
            used = "loose"
            seen: set = set()
            for m in _LOOSE.finditer(html_text):
                aid = m.group("aid")
                if aid in seen:
                    continue
                seen.add(aid)
                notices.append(Notice(
                    seq=0, article_id=aid,
                    title=html.unescape(m.group("title")).strip(),
                    posted_at=_to_date(m.group("d")), url=self.detail_url(aid),
                ))

        total = max((n.seq for n in notices), default=None) or None
        return notices, total, used
