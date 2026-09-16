"""IBK시스템 게시판 어댑터 (www.ibksystem.co.kr).

표가 아니라 div 목록이다. 한 건이 bbs-list-row 블록 하나에 들어 있고,
제목 링크(/board/read?brdId=...&atclNo=UUID)와 등록일이 같은 블록 안에 있다.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

_LINK = re.compile(r'href="(?P<href>/board/read\?[^"]*atclNo=(?P<aid>[^"&]+))"[^>]*>(?P<title>.*?)</a>', re.S)
_DATE = re.compile(r'등록일"><span>(?P<d>\d{4}-\d{2}-\d{2})</span>')
_SEQ = re.compile(r'bbs-no-data"><span>(?P<seq>\d+)</span>')


def _clean(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


class IbkSystemAdapter(BaseAdapter):
    adapter_id = "ibksystem"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}/board?brdId={self._p('board_id', 'BRD002')}&page={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}/board?brdId={self._p('board_id', 'BRD002')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}/board/read?brdId={self._p('board_id', 'BRD002')}&atclNo={article_id}"

    def validate(self, html_text: str) -> bool:
        return "bbs-list-row" in html_text or "/board/read?" in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        chunks = html_text.split('bbs-list-row')[1:]
        for chunk in chunks:
            link = _LINK.search(chunk)
            dt = _DATE.search(chunk)
            if not link or not dt:
                continue
            title = _clean(link.group('title'))
            if not title:
                continue
            seq = _SEQ.search(chunk)
            notices.append(Notice(
                seq=int(seq.group('seq')) if seq else 0,
                article_id=html.unescape(link.group('aid')),
                title=title, posted_at=_to_date(dt.group('d')),
                url=self.spec.base + html.unescape(link.group('href')),
            ))
        used = "strict"

        if not notices:  # 블록 구조가 바뀌어도 링크와 날짜만 있으면 복구
            used = "loose"
            seen: set = set()
            for m in _LINK.finditer(html_text):
                aid = html.unescape(m.group('aid'))
                if aid in seen:
                    continue
                tail = html_text[m.end():m.end() + 500]
                dt = re.search(r'(\d{4}-\d{2}-\d{2})', tail)
                title = _clean(m.group('title'))
                if not dt or not title:
                    continue
                seen.add(aid)
                notices.append(Notice(
                    seq=0, article_id=aid, title=title,
                    posted_at=_to_date(dt.group(1)),
                    url=self.spec.base + html.unescape(m.group('href')),
                ))
        # 이 게시판의 번호는 페이지마다 1부터 다시 시작해 전체 건수로 쓸 수 없다.
        return notices, None, used
