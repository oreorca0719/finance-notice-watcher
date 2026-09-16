"""KB금융지주 공지사항 어댑터 (www.kbfg.com).

목록 페이지는 껍데기만 내려오고 실제 목록은 아래 API 가 JSON 으로 준다(실측).
  /api/kbfg/notics?bulbdId=9&page=1&pageSize=20&affcomCd=
상세는 view.htm?CONTENT=<bltcId>&B=<bulbdId> 로 열린다.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import List, Optional, Tuple

from core.adapters.base import BaseAdapter
from core.models import Notice


def _to_date(s: str) -> Optional[date]:
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(s or ''))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


class KbfgAdapter(BaseAdapter):
    adapter_id = "kbfg"

    def _p(self, key: str, default: str = "") -> str:
        return str(self.spec.params.get(key, default))

    def list_url(self, page: int) -> str:
        return (f"{self.spec.base}/api/kbfg/notics?bulbdId={self._p('board_id', '9')}"
                f"&page={page}&pageSize={self._p('page_size', '20')}&affcomCd=")

    def detail_url(self, article_id: str) -> str:
        return (f"{self.spec.base}{self._p('view_path', '/kor/pr/notice/view.htm')}"
                f"?CONTENT={article_id}&B={self._p('board_id', '9')}")

    def supports_detail(self) -> bool:
        return False

    def validate(self, text: str) -> bool:
        return '"posts"' in text or '"resultCode"' in text

    def parse_list(self, text: str) -> Tuple[List[Notice], Optional[int], str]:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RuntimeError(f"KB금융지주 응답이 JSON 이 아닙니다: {exc}")
        result = (data or {}).get("result") or {}
        posts = result.get("posts") or []
        notices: List[Notice] = []
        for p in posts:
            aid = str(p.get("bltcId") or "").strip()
            title = str(p.get("titl") or "").strip()
            posted = _to_date(p.get("rgcrYms"))
            if not aid or not title or not posted:
                continue
            notices.append(Notice(
                seq=int(p.get("rn") or 0), article_id=aid, title=title,
                posted_at=posted, url=self.detail_url(aid),
            ))
        total = (result.get("paging") or {}).get("totalCount")
        return notices, (int(total) if total else None), "json"
