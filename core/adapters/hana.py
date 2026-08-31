"""하나은행 새소식 어댑터 (www.kebhana.com 새소식/이벤트).

게시판 특성:
  · 목록·상세 모두 서버 렌더링. 로그인·JS 불필요
  · 페이지네이션은 index,1,list,{page}.jsp 이고 1페이지는 index.jsp (마지막 숫자가 페이지)
  · 상세 URL 은 {article_id}_115430.jsp 이며 article_id 가 곧 식별자
  · KB 와 달리 상세 페이지에 공고 본문 전문이 들어 있다(첨부를 열지 않아도 내용 파악 가능)
  · 첨부는 image.kebhana.com 절대경로 GET 링크
  · 입찰공고가 서비스 점검 안내 등 일반 공지와 섞여 있어 제목 필터 의존도가 높다
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_ROW = re.compile(
    r'<li>\s*<span class="tit">\s*<a href="(?P<href>[^"]*?(?P<aid>\d{5,})_\d+\.jsp)"\s*>'
    r'(?P<title>.*?)</a>\s*</span>\s*<span class="date">(?P<d>\d{4}-\d{2}-\d{2})</span>',
    re.S,
)
_LOOSE = re.compile(
    r'/cont/news/news01/(?P<aid>\d{5,})_\d+\.jsp"[^>]*>(?P<title>[^<]{4,200})</a>'
    r'.{0,200}?(?P<d>\d{4}-\d{2}-\d{2})',
    re.S,
)
_LAST_PAGE = re.compile(r'index,1,list,(\d+)\.jsp"[^>]*title="끝 페이지로 이동"')
_FILE = re.compile(
    r'<a[^>]*class="btnBox[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<name>.*?)</a>', re.S)
_FILE_ALT = re.compile(
    r'<a[^>]+href="(?P<href>https?://image\.kebhana\.com/[^"]+)"[^>]*>(?P<name>.*?)</a>', re.S)
_BODY = re.compile(r'<td colspan="2" class="cont">(?P<v>.*?)</td>', re.S)


def _txt(raw: str) -> str:
    raw = re.sub(r'(?is)<br\s*/?>', '\n', raw)
    raw = re.sub(r'(?is)</(p|li|h\d|tr|div)>', '\n', raw)
    raw = html.unescape(re.sub(r'(?is)<[^>]+>', '', raw))
    lines = [re.sub(r'[ \t\xa0]+', ' ', ln).strip() for ln in raw.split('\n')]
    return '\n'.join(ln for ln in lines if ln)


def _one(raw: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'(?is)<[^>]+>', ' ', raw))).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('-'))
    return date(y, m, d)


class HanaAdapter(BaseAdapter):
    adapter_id = "hana"

    _DIR = "/cont/news/news01"

    def list_url(self, page: int) -> str:
        # 1페이지만 경로가 다르다. 이 예외를 놓치면 1페이지가 조회되지 않는다.
        if page <= 1:
            return f"{self.spec.base}{self._DIR}/index.jsp"
        return f"{self.spec.base}{self._DIR}/index,1,list,{page}.jsp"

    def detail_url(self, article_id: str) -> str:
        suffix = str(self.spec.params.get("view_suffix", "115430"))
        return f"{self.spec.base}{self._DIR}/{article_id}_{suffix}.jsp"

    def validate(self, html_text: str) -> bool:
        return 'news_list' in html_text or '_115430.jsp' in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group('aid')
            notices.append(Notice(
                seq=int(aid), article_id=aid, title=_one(m.group('title')),
                posted_at=_to_date(m.group('d')), url=self.detail_url(aid),
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
                        seq=int(aid), article_id=aid, title=title,
                        posted_at=_to_date(m.group('d')), url=self.detail_url(aid),
                    ))

        # 전체 건수는 표기되지 않으므로 마지막 페이지 번호 x 10 으로 근사한다.
        lp = _LAST_PAGE.search(html_text)
        total = int(lp.group(1)) * 10 if lp else None
        return notices, total, used

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        atts: List[Attachment] = []
        seen_href: set = set()
        for rx in (_FILE, _FILE_ALT):
            for m in rx.finditer(html_text):
                href = html.unescape(m.group('href'))
                if href in seen_href or href.startswith(('#', 'javascript')):
                    continue
                seen_href.add(href)
                name = _one(m.group('name')) or href.rsplit('/', 1)[-1]
                atts.append(Attachment(
                    display_name=name,
                    url=href if href.startswith('http') else self.spec.base + href,
                ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice

    def body_text(self, html_text: str) -> str:
        """상세 본문. 하나은행은 여기에 공고 전문이 들어 있다."""
        m = _BODY.search(html_text)
        return _txt(m.group('v')) if m else ""
