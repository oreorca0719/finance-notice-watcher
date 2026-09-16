"""우리은행 공고 어댑터 (spot.wooribank.com).

목록 페이지에는 공고가 없고, 페이지가 열린 뒤 아래 주소로 POST 해서 JSON 을 받는다(실측).
  POST /pot/jcc?withyou=BPPBC0038&__ID=c064827   body: START_NO=<페이지>&BOARD_ID=B00072
세션 쿠키가 필요해 목록 페이지를 먼저 열고 POST 한다.
상세는 POST 로만 열려 GET 주소를 만들 수 없다. 메일 링크는 목록 페이지로 건다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice

log = logging.getLogger(__name__)


def _to_date(s: str) -> Optional[date]:
    m = re.match(r'(\d{4})[.\-](\d{2})[.\-](\d{2})', str(s or ''))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


class WooriBankAdapter(BaseAdapter):
    adapter_id = "wooribank"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return f"{self.spec.base}{self._p('list_path')}"

    def detail_url(self, article_id: str) -> str:
        return self.list_url(1)

    def supports_detail(self) -> bool:
        return False

    def validate(self, text: str) -> bool:
        return "BPPBC0038" in text or "RESULTCODE" in text

    # 목록을 POST 로 받아야 해 공통 흐름 대신 직접 구현한다.
    def collect(self) -> Tuple[List[Notice], Optional[int]]:
        # 세션 쿠키 확보용으로 목록 페이지를 먼저 연다(수집 전략 강등도 여기서 동작한다).
        self.fetcher.get(self.list_url(1), validator=self.validate)
        found: List[Notice] = []
        total: Optional[int] = None
        for page in range(1, self.spec.list_pages + 1):
            body = self.fetcher.post(f"{self.spec.base}{self._p('api_path')}", {
                "START_NO": str(page),
                "BOARD_ID": self._p("board_id", "B00072"),
            })
            items, tot, used = self.parse_list(body.decode("utf-8", "replace"))
            self.parser_used = used
            total = total or tot
            if not items:
                raise RuntimeError(
                    f"{self.spec.name} {page}페이지에서 공고를 하나도 받지 못했습니다. "
                    f"목록 조회 방식이 바뀌었을 수 있습니다."
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
        return uniq, total

    def parse_list(self, text: str) -> Tuple[List[Notice], Optional[int], str]:
        try:
            data = json.loads(text.strip())
        except ValueError as exc:
            raise RuntimeError(f"우리은행 응답이 JSON 이 아닙니다: {exc}")
        if str(data.get("RESULTCODE")) != "SUCCESS":
            raise RuntimeError(f"우리은행 응답 코드: {data.get('RESULTCODE')}")
        notices: List[Notice] = []
        for r in data.get("RESULT") or []:
            aid = str(r.get("ARTICLE_ID") or "").strip()
            title = str(r.get("TITLE") or "").strip()
            posted = _to_date(r.get("M_DATE"))
            if not aid or not title or not posted:
                continue
            notices.append(Notice(
                seq=int(r.get("RNUM") or 0), article_id=aid, title=title,
                posted_at=posted, url=self.list_url(1),
            ))
        total = data.get("TOTALROW")
        return notices, (int(total) if str(total).isdigit() else None), "json"
