"""하나금융TI 전자구매시스템 어댑터 (tipartner.hanati.co.kr).

입찰공지는 로그인 없이 볼 수 있지만 화면에서 '입찰공지'를 눌러 여는 팝업 안에 있다.
팝업은 POST 로 열리고 CSRF 토큰을 요구한다. 토큰 없이 POST 하면 403 이다(실측).

  1) /spLogin.do 를 열어 세션 쿠키와 <meta name="_csrf"> 토큰을 얻는다
  2) /sp/mn/bidding/bidNoticeLink.do 에 _csrf 와 page 를 실어 POST 하면 목록 HTML 이 온다

상세도 같은 방식의 POST 라 GET 주소를 만들 수 없다. 메일 링크는 첫 화면으로 건다.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

log = logging.getLogger(__name__)

_CSRF = re.compile(r'<meta name="_csrf" content="([^"]+)"')
_ROW = re.compile(
    r'<tr>\s*<td>(?P<seq>\d+)</td>\s*'
    r'<td><a[^>]*data-id="(?P<aid>[^"]+)"[^>]*>(?P<title>.*?)</a></td>\s*'
    r'<td>(?P<way>[^<]*)</td>\s*<td>[^<]*</td>\s*<td>(?P<d>\d{4}-\d{2}-\d{2})</td>',
    re.S,
)
_LOOSE = re.compile(r'data-id="(?P<aid>[^"]+)"[^>]*>(?P<title>[^<]{4,200})</a>.{0,300}?(?P<d>\d{4}-\d{2}-\d{2})', re.S)


def _clean(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


class HanaTiAdapter(BaseAdapter):
    adapter_id = "hanati"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._p('home_path', '/spLogin.do')}"

    def detail_url(self, article_id: str) -> str:
        return self.list_url(1)

    def supports_detail(self) -> bool:
        return False

    def validate(self, html_text: str) -> bool:
        return "_csrf" in html_text or "bidNoticeList(" in html_text

    # 목록이 CSRF 토큰을 요구하는 POST 팝업이라 공통 흐름 대신 직접 구현한다.
    def collect(self) -> Tuple[List[Notice], Optional[int]]:
        home = self.fetcher.get(self.list_url(1), validator=self.validate)
        token = _CSRF.search(home)
        if not token:
            raise RuntimeError("하나금융TI 첫 화면에서 CSRF 토큰을 찾지 못했습니다. 화면 구조가 바뀌었을 수 있습니다.")

        found: List[Notice] = []
        for page in range(1, self.spec.list_pages + 1):
            body = self.fetcher.post(
                f"{self.spec.base}{self._p('list_path', '/sp/mn/bidding/bidNoticeLink.do')}",
                {"_csrf": token.group(1), "page": str(page)},
            ).decode("utf-8", "replace")
            items, _, used = self.parse_list(body)
            self.parser_used = used
            if not items:
                raise RuntimeError(
                    f"{self.spec.name} {page}페이지에서 입찰공지를 하나도 파싱하지 못했습니다. "
                    f"팝업 구조 변경이 의심됩니다."
                )
            for n in items:
                n.source_id = self.spec.id
                n.source_name = self.spec.name
            found.extend(items)

        uniq, seen = [], set()
        for n in found:
            if n.article_id in seen:
                continue
            seen.add(n.article_id)
            uniq.append(n)
        if len(uniq) < len(found):
            log.info("[%s] 페이지 간 중복 %d건 제거", self.spec.id, len(found) - len(uniq))
        return uniq, None

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            title = _clean(m.group('title'))
            if not title:
                continue
            way = _clean(m.group('way'))
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=html.unescape(m.group('aid')),
                title=f"{title} ({way})" if way else title,
                posted_at=_to_date(m.group('d')), url=self.list_url(1),
            ))
        used = "strict"

        if not notices:  # 표 구조가 바뀌어도 글번호와 날짜만 있으면 복구
            used = "loose"
            seen: set = set()
            for m in _LOOSE.finditer(html_text):
                aid = html.unescape(m.group('aid'))
                if aid in seen:
                    continue
                seen.add(aid)
                notices.append(Notice(seq=0, article_id=aid, title=_clean(m.group('title')),
                                      posted_at=_to_date(m.group('d')), url=self.list_url(1)))
        return notices, None, used
