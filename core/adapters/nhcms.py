"""농협 계열 공통 CMS 어댑터 (nhfngroup.com / nhabgroup.com / nhseoul.nonghyup.com …).

NH농협금융지주·농협경제지주·지역 농협이 같은 게시판 솔루션을 쓴다.
따라서 어댑터는 하나만 두고 config.yaml 에서 siteId/boardId 만 달리 지정한다.

게시판 특성:
  · 목록 행: td.boardSeq / td.title > a[href=boardList.do?...boardSeq=N] / td.regDate
  · 등록일 구분자가 사이트마다 다르다 (2026.08.27 과 2026-08-18 이 공존) → 둘 다 처리
  · 상세도 같은 boardList.do 에 command=view 로 접근한다
  · 첨부는 목록에 아이콘만 있고 실제 링크는 상세에 있다
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_ROW = re.compile(
    r'<td class="boardSeq">\s*(?P<seq>\d+)</td>\s*'
    r'<td class="title">\s*<a\s+href=[\'"](?P<href>[^\'"]*boardSeq=(?P<aid>\d+)[^\'"]*)[\'"]\s*>'
    r'(?P<title>.*?)</a>.*?'
    r'<td class="regDate">\s*(?P<d>\d{4}[.\-]\d{2}[.\-]\d{2})\s*</td>',
    re.S,
)
_LOOSE = re.compile(
    r'boardSeq=(?P<aid>\d+)[^>]*>\s*(?P<title>[^<]{4,200})\s*(?:&nbsp;)?\s*</a>'
    r'.{0,400}?(?P<d>\d{4}[.\-]\d{2}[.\-]\d{2})',
    re.S,
)
# 첨부는 file_download2('siteId','fileSeq') 자바스크립트로 걸려 있고
# 실제 경로는 /common/downLoad.do?siteId=..&fileSeq=.. 다(페이지 내 함수 정의로 확인).
_FILE = re.compile(
    r"file_download2\(\s*'(?P<site>[^']+)'\s*,\s*'(?P<seq>\d+)'\s*\)"
    r"(?P<tail>.{0,300}?)</a>",
    re.S,
)


def _one(raw: str) -> str:
    raw = re.sub(r'(?is)<[^>]+>', ' ', raw)
    return re.sub(r'\s+', ' ', html.unescape(raw)).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in re.split(r'[.\-]', s.strip()))
    return date(y, m, d)


class NhCmsAdapter(BaseAdapter):
    adapter_id = "nhcms"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def _base_query(self) -> str:
        q = f"siteId={self._p('site_id')}"
        if self._p('board_id'):
            q += f"&boardId={self._p('board_id')}"
        if self._p('menu_seq'):
            q += f"&codyMenuSeq={self._p('menu_seq')}"
        return q

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}/user/boardList.do?{self._base_query()}&page={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        if page != 1 or not self._p('menu_seq'):
            return None
        return (f"{self.spec.base}/user/indexSub.do?"
                f"codyMenuSeq={self._p('menu_seq')}&siteId={self._p('site_id')}")

    def detail_url(self, article_id: str) -> str:
        return (f"{self.spec.base}/user/boardList.do?command=view&page=1"
                f"&{self._base_query()}&boardSeq={article_id}")

    def validate(self, html_text: str) -> bool:
        return 'boardSeq' in html_text and ('class="title"' in html_text or 'boardList.do' in html_text)

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group('aid')
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=aid,
                title=_one(m.group('title')), posted_at=_to_date(m.group('d')),
                url=self.detail_url(aid),
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
            key = (m.group('site'), m.group('seq'))
            if key in seen:
                continue
            seen.add(key)
            raw = m.group('tail')
            if '>' in raw:
                raw = raw.split('>', 1)[1]
            name = _one(raw) or f"첨부_{m.group('seq')}"
            atts.append(Attachment(
                display_name=name,
                url=f"{self.spec.base}/common/downLoad.do"
                    f"?siteId={m.group('site')}&fileSeq={m.group('seq')}",
                referer=notice.url,
            ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
