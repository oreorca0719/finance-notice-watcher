"""표준형 게시판 어댑터 — 신한자산운용 / 우리금융저축은행.

두 사이트는 마크업이 다르지만 '목록 링크에 상세 URL 이 그대로 들어 있고
같은 행에 날짜가 붙는' 동일 패턴이라 하나의 어댑터로 처리한다.
사이트별 차이는 config.yaml 의 params.variant 로 가른다.

  variant: shinhanfund   신한자산운용  td.tb-subj > a[/ko/pc/board/noticeView?no=N], 같은 tr 의 td 에 날짜
  variant: woorisb       우리금융저축은행 li.board__item.name > a[/intro-notice/view.do?blcSn=N],
                         li.board__item.date 에 날짜
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

_SHF_ROW = re.compile(
    r'<td>\s*<span class="fc-\d+">(?P<seq>\d+)</span>\s*</td>\s*'
    r'<td class="tb-subj">\s*<a href="(?P<href>[^"]*noticeView\?no=(?P<aid>\d+))"\s*>'
    r'(?P<title>.*?)</a>.*?'
    r'<td>\s*(?P<d>\d{4}\.\d{2}\.\d{2})\s*</td>',
    re.S,
)
_WSB_ROW = re.compile(
    r'<li class="board__item name">.*?'
    r'<a href="(?P<href>[^"]*view\.do\?blcSn=(?P<aid>\d+)[^"]*)"\s*>\s*'
    r'<em>(?P<title>.*?)</em>.*?</a>.*?'
    r'<li class="board__item date">.*?(?P<d>\d{4}-\d{2}-\d{2})</li>',
    re.S,
)
_LOOSE = re.compile(
    r'(?:no|blcSn)=(?P<aid>\d+)[^>]*>\s*(?:<em>)?(?P<title>[^<]{4,200})(?:</em>)?\s*</a>'
    r'.{0,500}?(?P<d>\d{4}[.\-]\d{2}[.\-]\d{2})',
    re.S,
)
# 우리금융저축은행은 fileSn 만 노출하고 atchFileId 가 없어 다운로드 경로를
# 확정할 수 없다. 파일명만 수집해 메일에 표시하고 다운로드는 건너뛴다
# (config 의 params.attachments: false).
_WSB_FILE = re.compile(
    r'<a class="btn_file_download"[^>]*fileSn="(?P<sn>\d+)"[^>]*title="(?P<name>[^"]*?)다운로드"',
    re.S,
)
_FILE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*(?:[Ff]ile|down[Ll]oad|Download|atchFile)[^"]*)"[^>]*>'
    r'(?P<name>.*?)</a>',
    re.S,
)


def _one(raw: str) -> str:
    raw = re.sub(r'(?is)<[^>]+>', ' ', raw)
    return re.sub(r'\s+', ' ', html.unescape(raw)).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in re.split(r'[.\-]', s.strip()))
    return date(y, m, d)


class SimpleBoardAdapter(BaseAdapter):
    adapter_id = "simpleboard"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    @property
    def variant(self) -> str:
        return self._p("variant", "shinhanfund")

    def list_url(self, page: int) -> str:
        path = self._p("list_path")
        sep = "&" if "?" in path else "?"
        key = self._p("page_param", "page")
        return f"{self.spec.base}{path}{sep}{key}={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._p('list_path')}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}{self._p('view_path')}{article_id}"

    def validate(self, html_text: str) -> bool:
        marker = self._p("marker", "")
        return marker in html_text if marker else len(html_text) > 2000

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        rx = _SHF_ROW if self.variant == "shinhanfund" else _WSB_ROW
        notices: List[Notice] = []
        for m in rx.finditer(html_text):
            aid = m.group('aid')
            gd = m.groupdict()
            notices.append(Notice(
                seq=int(gd.get('seq') or 0),
                article_id=aid,
                title=_one(m.group('title')),
                posted_at=_to_date(m.group('d')),
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
        if self.variant == "woorisb":
            for m in _WSB_FILE.finditer(html_text):
                if m.group('sn') in seen:
                    continue
                seen.add(m.group('sn'))
                atts.append(Attachment(display_name=html.unescape(m.group('name'))))
            notice.attachments = atts
            notice.detail_ok = True
            return notice
        for m in _FILE.finditer(html_text):
            href = html.unescape(m.group('href'))
            if href in seen or href.startswith(('#', 'javascript')):
                continue
            seen.add(href)
            name = _one(m.group('name'))
            if not name or len(name) > 200:
                continue
            atts.append(Attachment(
                display_name=name,
                url=href if href.startswith('http') else self.spec.base + href,
                referer=notice.url,
            ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
