"""우리금융그룹 공고 어댑터 (www.woorifg.com PR센터 > 공고).

게시판 특성:
  · 목록·상세 모두 서버 렌더링. 로그인·JS 불필요
  · 페이지네이션은 list.do?pageIndex=N (page/currentPageNo 는 무시되므로 주의)
  · 공고번호가 없고 seq 파라미터가 곧 식별자
  · 첨부는 단순 GET 링크(/cmm/fms/FileDown.do?fileId=...)
  · 입찰공고와 모집공고·주식교환공고 등이 섞여 있어 제목 필터 의존도가 높다
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_ROW = re.compile(
    r'<div class="list">\s*<div class="subj">\s*<a\s+href="(?P<href>[^"]+)"\s*>'
    r'(?P<title>.*?)</a>.*?<div class="date[^"]*">(?P<d>\d{4}\.\d{2}\.\d{2})</div>',
    re.S,
)
_LOOSE = re.compile(
    r'view\.do\?seq=(?P<seq>\d+)[^>]*>(?P<title>[^<]{4,200})</a>'
    r'.{0,300}?(?P<d>\d{4}\.\d{2}\.\d{2})',
    re.S,
)
_TOTAL = re.compile(r'총 게시글\s*<span[^>]*>([\d,]+)</span>')
_SEQ = re.compile(r'seq=(\d+)')
_FILE = re.compile(r'<a\s+href="(?P<href>[^"]*FileDown\.do[^"]*)"[^>]*>(?P<name>.*?)</a>', re.S)
_VIEW_TITLE = re.compile(r'<h[23][^>]*class="[^"]*(?:tit|subj)[^"]*"[^>]*>(?P<v>.*?)</h[23]>', re.S)


def _txt(raw: str) -> str:
    raw = html.unescape(re.sub(r'(?is)<[^>]+>', ' ', raw))
    return re.sub(r'\s+', ' ', raw).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('.'))
    return date(y, m, d)


class WooriFgAdapter(BaseAdapter):
    adapter_id = "woorifg"

    _LIST = "/kor/pr/announcement/list.do"
    _VIEW = "/kor/pr/announcement/view.do"

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._LIST}?pageIndex={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._LIST}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}{self._VIEW}?seq={article_id}"

    def validate(self, html_text: str) -> bool:
        return 'view.do?seq=' in html_text and 'announcement' in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            sm = _SEQ.search(m.group('href'))
            if not sm:
                continue
            seq = sm.group(1)
            notices.append(Notice(
                seq=int(seq), article_id=seq, title=_txt(m.group('title')),
                posted_at=_to_date(m.group('d')), url=self.detail_url(seq),
            ))
        used = 'strict'

        if not notices:  # 구조 개편 대비 폴백
            used = 'loose'
            seen: set = set()
            for m in _LOOSE.finditer(html_text):
                seq = m.group('seq')
                if seq in seen:
                    continue
                seen.add(seq)
                title = _txt(m.group('title'))
                if title:
                    notices.append(Notice(
                        seq=int(seq), article_id=seq, title=title,
                        posted_at=_to_date(m.group('d')), url=self.detail_url(seq),
                    ))

        tm = _TOTAL.search(html_text)
        return notices, (int(tm.group(1).replace(',', '')) if tm else None), used

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        atts: List[Attachment] = []
        for m in _FILE.finditer(html_text):
            href = html.unescape(m.group('href'))
            name = _txt(m.group('name'))
            if not name:
                continue
            atts.append(Attachment(
                display_name=name,
                url=href if href.startswith('http') else self.spec.base + href,
            ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
