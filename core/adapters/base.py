"""기관 어댑터 인터페이스.

금융사마다 게시판 구조가 전혀 다르므로, 사이트별 지식은 전부 어댑터 안에 가둔다.
watcher 는 이 인터페이스만 알면 되고, 기관을 추가할 때 watcher 를 고칠 일이 없다.

새 기관을 붙이는 방법:
  1) core/adapters/<기관>.py 에 BaseAdapter 를 상속한 클래스를 만든다
  2) list_pages() / parse_list() 를 구현한다 (상세·첨부는 선택)
  3) core/adapters/__init__.py 의 REGISTRY 에 등록한다
  4) config.yaml 의 sources 에 항목을 추가한다
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.fetcher import Fetcher
from core.models import Attachment, Notice

log = logging.getLogger(__name__)


class BaseAdapter:
    #: config.yaml 의 adapter 값과 대응하는 식별자
    adapter_id: str = "base"

    def __init__(self, spec, fetcher: Fetcher) -> None:
        self.spec = spec
        self.fetcher = fetcher
        self.parser_used: Optional[str] = None

    # ------------------------------------------------------------------
    # 하위 클래스가 구현해야 하는 것
    # ------------------------------------------------------------------
    def list_url(self, page: int) -> str:
        raise NotImplementedError

    def list_url_alt(self, page: int) -> Optional[str]:
        """1차 URL 이 막혔을 때 시도할 대체 URL. 없으면 None."""
        return None

    def validate(self, html_text: str) -> bool:
        """받아온 HTML 이 진짜 게시판인지 판별. 로그인·오류·WAF 페이지를 걸러낸다."""
        return bool(html_text) and len(html_text) > 500

    def parse_list(self, html_text: str) -> Tuple[List[Notice], Optional[int], str]:
        """(공고목록, 전체건수, 사용한파서명)"""
        raise NotImplementedError

    def parse_detail(self, html_text: str, notice: Notice) -> Notice:
        """상세에서 첨부 등을 채운다. 상세 페이지가 없는 기관은 그대로 반환."""
        return notice

    def detail_url(self, article_id: str) -> str:
        raise NotImplementedError

    def supports_detail(self) -> bool:
        return True

    def supports_attachment(self) -> bool:
        return True

    def download(self, attachment: Attachment) -> bytes:
        """첨부 바이너리. POST 폼 방식과 GET URL 방식을 모두 지원한다."""
        if attachment.action:
            return self.fetcher.post(self.spec.base + attachment.action, attachment.as_form())
        if attachment.url:
            return self.fetcher.get_bytes(attachment.url)
        raise RuntimeError(f"다운로드 경로가 없는 첨부: {attachment.display_name}")

    # ------------------------------------------------------------------
    # 공통 실행 흐름 (하위 클래스가 건드릴 필요 없음)
    # ------------------------------------------------------------------
    def collect(self) -> Tuple[List[Notice], Optional[int]]:
        found: List[Notice] = []
        total: Optional[int] = None
        for page in range(1, self.spec.list_pages + 1):
            html_text = self.fetcher.get(
                self.list_url(page),
                alt_url=self.list_url_alt(page),
                validator=self.validate,
            )
            items, tot, used = self.parse_list(html_text)
            self.parser_used = used
            total = total or tot
            if not items:
                raise RuntimeError(
                    f"{self.spec.name} {page}페이지에서 공고를 하나도 파싱하지 못했습니다. "
                    f"게시판 구조 변경이 의심됩니다."
                )
            for n in items:
                n.source_id = self.spec.id
                n.source_name = self.spec.name
            found.extend(items)
        return found, total

    def enrich(self, notices: List[Notice]) -> None:
        if not self.supports_detail():
            return
        for n in notices:
            try:
                self.parse_detail(self.fetcher.get(self.detail_url(n.article_id)), n)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] 상세 조회 실패 #%s: %s", self.spec.id, n.seq, exc)

    def describe(self) -> Dict[str, Any]:
        return {"id": self.spec.id, "name": self.spec.name, "adapter": self.adapter_id}
