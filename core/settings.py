"""config.yaml + .env 로딩 계층. 코드 수정 없이 운영값만 바꾸기 위한 단일 진입점."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class SourceSpec(BaseModel):
    """감시 대상 기관 1곳. 사이트별 세부값은 params 에 자유롭게 담는다.

    새 기관을 추가할 때 코드를 고칠 필요 없이 config.yaml 에 항목만 늘리면 된다.
    """

    id: str
    name: str
    adapter: str
    base: str
    enabled: bool = True
    list_pages: int = 3
    request_delay: float = 0.8
    timeout: int = 30
    warm_path: str = "/"
    #: 게시판 자체가 입찰공고 전용이면 require 게이트를 건너뛴다.
    #: 농협 e홍보센터처럼 제목에 '공고'가 없는 입찰 건이 섞인 경우 필요하다.
    skip_require: bool = False
    params: Dict[str, Any] = Field(default_factory=dict)


class FilterCfg(BaseModel):
    """공고 제목으로 프로젝트성 여부를 판별한다. 모든 기관에 공통 적용된다.

    판정 순서 (앞 단계에서 탈락하면 뒤는 보지 않는다):
      1) require : 하나도 없으면 탈락 — '공고' 자체가 아닌 글을 걸러내는 구조적 게이트.
                   하나은행 새소식처럼 입찰공고와 서비스 점검 안내가 섞인 게시판 대응.
      2) exclude : 하나라도 있으면 탈락 — 비IT·비금융 건.
      3) include : 하나라도 있으면 통과. 비어 있으면 전부 통과.
    """

    require_keywords: List[str] = Field(default_factory=list)
    include_keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)

    def accepts(self, title: str, skip_require: bool = False) -> bool:
        if not skip_require and self.require_keywords and not any(
                k in title for k in self.require_keywords if k):
            return False
        if any(k for k in self.exclude_keywords if k and k in title):
            return False
        if not self.include_keywords:
            return True
        return any(k in title for k in self.include_keywords if k)

    def reason(self, title: str, skip_require: bool = False) -> tuple:
        """(통과여부, 사유) — 진단·리포트용."""
        if not skip_require and self.require_keywords and not any(
                k in title for k in self.require_keywords if k):
            return False, "공고성 아님"
        ex = [k for k in self.exclude_keywords if k and k in title]
        if ex:
            return False, "제외:" + ",".join(ex[:2])
        inc = [k for k in self.include_keywords if k and k in title]
        if inc:
            return True, "적중:" + ",".join(inc[:2])
        return (not self.include_keywords), "키워드없음"


_EMAIL = re.compile(r"[^\s<>@]+@[^\s<>@]+\.[A-Za-z]{2,}")


def load_recipient_file(path: Path) -> List[str]:
    """recipients.txt 를 읽어 주소만 뽑는다.

    '홍길동 <hong@pron.co.kr>' 형태와 순수 주소를 모두 허용하고,
    주석(#)·빈 줄·중복은 제거한다. 명단 관리는 이 파일 하나로 끝난다.
    """
    if not path.exists():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _EMAIL.search(line)
        if m and m.group(0) not in out:
            out.append(m.group(0))
    return out


class MailCfg(BaseModel):
    recipients: List[str] = Field(default_factory=list)
    recipients_file: str = "recipients.txt"
    alert_recipients: List[str] = Field(default_factory=list)
    subject_prefix: str = "[금융권공고]"
    delivery_mode: str = "bcc"   # bcc | individual | to
    attach_pdf: bool = True
    max_attach_mb: int = 20
    send_when_empty: bool = False
    heartbeat_weekday: int = 0

    @property
    def alerts(self) -> List[str]:
        return self.alert_recipients or self.recipients


class RuntimeCfg(BaseModel):
    consecutive_failure_alert: int = 2
    stale_hours_alert: int = 30
    keep_html_snapshot: bool = True
    #: 일부 기관이 실패해도 나머지는 계속 진행한다. True 면 실패 기관을 경보로 알린다.
    alert_on_source_failure: bool = True


class SmtpCfg(BaseModel):
    """비밀번호는 config.yaml 이 아니라 .env 에서만 읽는다."""

    host: str = ""
    port: int = 587
    security: str = "starttls"
    user: str = ""
    password: str = ""
    from_name: str = "Project Searcher"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)


class Settings(BaseModel):
    sources: List[SourceSpec] = Field(default_factory=list)
    filter: FilterCfg = FilterCfg()
    mail: MailCfg = MailCfg()
    runtime: RuntimeCfg = RuntimeCfg()
    smtp: SmtpCfg = SmtpCfg()
    root: Path = ROOT

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        cfg_path = path or (ROOT / "config.yaml")
        # utf-8-sig 로 읽어 BOM 을 흡수한다. 편집기나 PowerShell 이 BOM 을 붙여도
        # 첫 키가 "﻿source" 로 깨져 설정 전체가 기본값으로 돌아가는 사고를 막는다.
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8-sig")) or {}
        smtp = SmtpCfg(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "587") or 587),
            security=os.getenv("SMTP_SECURITY", "starttls"),
            user=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            from_name=os.getenv("SMTP_FROM_NAME", "Project Searcher"),
        )
        mail = MailCfg(**raw.get("mail", {}))
        # config.yaml 의 목록 + recipients.txt 를 합치고 중복을 제거한다.
        from_file = load_recipient_file(ROOT / mail.recipients_file)
        merged = list(dict.fromkeys([*mail.recipients, *from_file]))
        mail.recipients = merged

        return cls(
            sources=[SourceSpec(**s) for s in (raw.get("sources") or [])],
            filter=FilterCfg(**raw.get("filter", {})),
            mail=mail,
            runtime=RuntimeCfg(**raw.get("runtime", {})),
            smtp=smtp,
        )

    @property
    def active_sources(self) -> List[SourceSpec]:
        return [s for s in self.sources if s.enabled]

    @property
    def data_dir(self) -> Path:
        d = self.root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def log_dir(self) -> Path:
        d = self.root / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d
