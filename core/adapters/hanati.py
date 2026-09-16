"""하나금융TI 전자구매시스템 어댑터 (tipartner.hanati.co.kr).

입찰공지 전체 목록과 상세는 로그인 뒤 팝업으로만 열리고, 로그인 없이 POST 하면 403 이다(실측).
다만 로그인 화면에 최근 입찰공지가 제목·날짜와 함께 노출되어 그 영역만 읽는다.

한계: 화면에 보이는 최근 몇 건만 감시한다. 하루에 그보다 많이 올라오면 놓칠 수 있다.
상세 주소를 만들 수 없어 메일 링크는 로그인 화면으로 건다.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

_ITEM = re.compile(
    r"bidNoticeList\('(?P<aid>[^']+)'\)[^>]*>(?P<title>.*?)</a>\s*<span class=\"date\">(?P<d>\d{4}-\d{2}-\d{2})</span>",
    re.S,
)


def _clean(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


class HanaTiAdapter(BaseAdapter):
    adapter_id = "hanati"

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self.spec.params.get('list_path', '/spLogin.do')}"

    def detail_url(self, article_id: str) -> str:
        return self.list_url(1)

    def supports_detail(self) -> bool:
        return False

    def validate(self, html_text: str) -> bool:
        return "bidNoticeList(" in html_text or "입찰공지" in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        seen: set = set()
        for m in _ITEM.finditer(html_text):
            aid = html.unescape(m.group('aid'))
            title = _clean(m.group('title'))
            if not title or aid in seen:
                continue
            seen.add(aid)
            notices.append(Notice(
                seq=0, article_id=aid, title=title,
                posted_at=_to_date(m.group('d')), url=self.list_url(1),
            ))
        return notices, len(notices) or None, "strict"
