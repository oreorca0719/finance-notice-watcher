"""농협 e홍보센터 입찰공고 어댑터 (www.nonghyup.com/ecenter/bid).

게시판 특성:
  · 목록·상세 모두 서버 렌더링. eGov 표준 게시판
  · 목록 페이징은 bidList.do?pageIndex=N
  · 상세 식별자는 blbdSqno (목록의 onclick 인자), 화면 번호는 별도의 seq
  · 첨부는 /cmm/fms/FileDown.do?atchFileId=...&fileSn=...
    **목록 → 상세를 먼저 거쳐 세션 쿠키를 확보해야 한다.**
    쿠키 없이 바로 받으면 404 가 떨어진다. Fetcher 가 세션을 유지하므로
    collect() → enrich() → download() 순서를 지키면 자연히 충족된다.
  · 농협은 은행 IT 사업과 물류센터 신축공사 등이 한 게시판에 섞여 있어
    제목 필터 의존도가 높다(시설공사 계열은 exclude_keywords 가 걸러낸다).
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Attachment, Notice

log = logging.getLogger(__name__)

_ROW = re.compile(
    r'<tr>\s*<td>(?P<seq>\d+)</td>\s*'
    r'<td class="tit_brdlist">\s*<a[^>]*onclick="fn_select_brdView\(\'(?P<aid>\d+)\'\);?"[^>]*>'
    r'(?P<title>.*?)</a>.*?'
    r'<td>(?P<d>\d{4}\.\d{2}\.\d{2})</td>\s*<td>(?P<hits>[\d,]+)</td>',
    re.S,
)
_LOOSE = re.compile(
    r"fn_select_brdView\('(?P<aid>\d+)'\);?[^>]*>(?P<title>[^<]{4,200})</a>"
    r".{0,400}?(?P<d>\d{4}\.\d{2}\.\d{2})",
    re.S,
)
_VIEW_TITLE = re.compile(r'<div class="view_tit">\s*<h4>(?P<v>.*?)</h4>', re.S)
_VIEW_DATE = re.compile(r'<dd title="등록일">(?P<v>\d{4}\.\d{2}\.\d{2})</dd>')
_VIEW_HITS = re.compile(r'<dd title="조회수">(?P<v>[\d,]+)</dd>')
_FILE = re.compile(
    r'fn_egov_downFile\(\s*[\'"](?P<fid>[^\'"]+)[\'"]\s*,\s*[\'"](?P<sn>\d+)[\'"]\s*\)'
    r'(?P<tail>.{0,300}?)</a>',
    re.S,
)


def _one(raw: str) -> str:
    raw = re.sub(r'(?is)<[^>]+>', ' ', raw)
    return re.sub(r'\s+', ' ', html.unescape(raw)).strip()


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.strip().split('.'))
    return date(y, m, d)


class NonghyupAdapter(BaseAdapter):
    adapter_id = "nonghyup"

    _LIST = "/ecenter/bid/bidList.do"
    _VIEW = "/ecenter/bid/bidView.do"
    _DOWN = "/cmm/fms/FileDown.do"

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._LIST}?pageIndex={page}"

    def list_url_alt(self, page: int) -> Optional[str]:
        return f"{self.spec.base}{self._LIST}" if page == 1 else None

    def detail_url(self, article_id: str) -> str:
        return f"{self.spec.base}{self._VIEW}?blbdSqno={article_id}"

    def validate(self, html_text: str) -> bool:
        return 'fn_select_brdView' in html_text or 'tit_brdlist' in html_text

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        notices: List[Notice] = []
        for m in _ROW.finditer(html_text):
            aid = m.group('aid')
            notices.append(Notice(
                seq=int(m.group('seq')), article_id=aid,
                title=_one(m.group('title')), posted_at=_to_date(m.group('d')),
                hits=int(m.group('hits').replace(',', '')), url=self.detail_url(aid),
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

        # 전체 건수 표기가 없어 목록의 최대 번호를 근사치로 쓴다.
        total = max((n.seq for n in notices), default=None) or None
        return notices, total, used

    def download(self, attachment: Attachment) -> bytes:
        """첨부 다운로드 직전에 해당 공고의 상세 페이지를 다시 연다.

        농협 서버는 '그 공고의 상세를 방금 조회한 세션'에만 파일을 내준다.
        상세 조회 후 다른 페이지를 거치면 404 가 떨어진다(실측 확인).
        수집 → 상세 → (다른 기관 처리) → 다운로드 순서로 도는 우리 흐름에서는
        반드시 여기서 상세를 한 번 더 열어야 한다.
        """
        if attachment.referer:
            try:
                self.fetcher.get(attachment.referer)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] 다운로드용 상세 재조회 실패: %s", self.spec.id, exc)
        return super().download(attachment)

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        tm = _VIEW_TITLE.search(html_text)
        if tm:
            notice.title = _one(tm.group('v')) or notice.title
        dm = _VIEW_DATE.search(html_text)
        if dm:
            notice.posted_at = _to_date(dm.group('v'))
        hm = _VIEW_HITS.search(html_text)
        if hm:
            notice.hits = int(hm.group('v').replace(',', ''))

        atts: List[Attachment] = []
        seen: set = set()
        for m in _FILE.finditer(html_text):
            fid, sn = m.group('fid'), m.group('sn')
            if (fid, sn) in seen:
                continue
            seen.add((fid, sn))
            # tail 은 여는 <a ...> 태그의 나머지부터 잡히므로 '">' 앞부분을 버린다.
            raw = m.group('tail')
            if '>' in raw:
                raw = raw.split('>', 1)[1]
            name = _one(raw) or f"{fid}_{sn}"
            atts.append(Attachment(
                display_name=name,
                url=f"{self.spec.base}{self._DOWN}?atchFileId={fid}&fileSn={sn}",
                referer=notice.url,
            ))
        notice.attachments = atts
        notice.detail_ok = True
        return notice
