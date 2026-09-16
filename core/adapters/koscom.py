"""코스콤 입찰공고 어댑터 (www.koscom.co.kr, 전자정부 표준 게시판).

목록은 표이고 상세 링크에 nttId 가 들어 있다. 제목에 마감 표시용 span 이 섞여 있어
앵커 안쪽 태그를 모두 지우고 글자만 쓴다.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

_ROW = re.compile(r'<tr>(?P<row>.*?)</tr>', re.S)
_LINK = re.compile(r'<a href="(?P<href>[^"]*view\.do\?nttId=(?P<aid>\d+)[^"]*)"[^>]*>(?P<title>.*?)</a>', re.S)
_SEQ = re.compile(r'<td>\s*(?P<seq>\d+)\s*</td>')
_DATE = re.compile(r'(?P<d>\d{4}[-.]\d{2}[-.]\d{2})')


def _clean(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in re.split(r'[-.]', s))
    return date(y, m, d)


class KoscomAdapter(BaseAdapter):
    adapter_id = "koscom"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return (f"{self.spec.base}{self._p('list_path')}"
                f"?menuNo={self._p('menu_no')}&pageIndex={page}")

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._p('list_path')}?menuNo={self._p('menu_no')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return (f"{self.spec.base}{self._p('view_path')}"
                f"?nttId={article_id}&menuNo={self._p('menu_no')}")

    def validate(self, html_text: str) -> bool:
        return "view.do?nttId=" in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for row in (m.group('row') for m in _ROW.finditer(html_text)):
            link = _LINK.search(row)
            if not link:
                continue
            title = _clean(link.group('title'))
            dt = _DATE.search(re.sub(r'<a .*?</a>', ' ', row, flags=re.S))
            if not title or not dt:
                continue
            seq = _SEQ.search(row)
            notices.append(Notice(
                seq=int(seq.group('seq')) if seq else 0,
                article_id=link.group('aid'), title=title,
                posted_at=_to_date(dt.group('d')),
                url=self.detail_url(link.group('aid')),
            ))
        used = "strict"

        if not notices:  # 표 구조가 바뀌어도 상세 링크와 날짜만 있으면 복구
            used = "loose"
            seen: set = set()
            for m in _LINK.finditer(html_text):
                aid = m.group('aid')
                if aid in seen:
                    continue
                dt = _DATE.search(html_text[m.end():m.end() + 300])
                title = _clean(m.group('title'))
                if not dt or not title:
                    continue
                seen.add(aid)
                notices.append(Notice(seq=0, article_id=aid, title=title,
                                      posted_at=_to_date(dt.group('d')),
                                      url=self.detail_url(aid)))
        return notices, (max((n.seq for n in notices), default=None) or None), used
