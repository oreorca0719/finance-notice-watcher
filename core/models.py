"""도메인 모델.

다중 금융사 지원을 위해 Notice 가 자신이 어느 기관에서 왔는지(source_id/source_name)를
스스로 알고 있어야 한다. 중복 판정과 메일 그룹핑이 모두 이 값에 의존한다.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    display_name: str
    server_name: str = ""
    # 다운로드 방식은 기관마다 다르다. GET 이면 url 을, POST 폼이면 action+form 을 쓴다.
    url: Optional[str] = None
    action: Optional[str] = None
    form: Dict[str, str] = Field(default_factory=dict)
    #: 일부 사이트는 상세 페이지를 Referer 로 요구한다.
    referer: Optional[str] = None

    def as_form(self) -> Dict[str, str]:
        return dict(self.form)


class Notice(BaseModel):
    source_id: str = "kb"
    source_name: str = "KB국민은행"
    seq: int = 0
    article_id: str
    title: str
    posted_at: date
    hits: int = 0
    url: str
    attachments: List[Attachment] = Field(default_factory=list)
    detail_ok: bool = False

    @property
    def key(self) -> str:
        """중복 판정 키. 기관이 달라도 공고번호가 겹칠 수 있으므로 반드시 조합해서 쓴다."""
        return f"{self.source_id}:{self.article_id}"


class SourceResult(BaseModel):
    """기관 1곳의 조회 결과. 한 곳이 실패해도 나머지는 계속 진행되어야 하므로 개별 기록."""

    source_id: str
    source_name: str
    ok: bool = False
    scanned: int = 0
    strategy: Optional[str] = None
    parser: Optional[str] = None
    total: Optional[int] = None
    new_notices: List[Notice] = Field(default_factory=list)
    error: Optional[str] = None


class RunResult(BaseModel):
    ok: bool = False
    sources: List[SourceResult] = Field(default_factory=list)
    mailed: bool = False
    error: Optional[str] = None

    @property
    def scanned(self) -> int:
        return sum(s.scanned for s in self.sources)

    @property
    def new_notices(self) -> List[Notice]:
        out: List[Notice] = []
        for s in self.sources:
            out.extend(s.new_notices)
        return out

    @property
    def failed_sources(self) -> List[SourceResult]:
        return [s for s in self.sources if not s.ok]

    def summary(self) -> Dict[str, Any]:
        return {
            "기관": len(self.sources),
            "성공": len([s for s in self.sources if s.ok]),
            "실패": len(self.failed_sources),
            "조회": self.scanned,
            "신규": len(self.new_notices),
        }
