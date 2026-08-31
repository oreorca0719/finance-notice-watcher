"""오케스트레이션 계층.

1회 실행 흐름:
  기관별 [수집 → 파싱 → 신규 판별] → 전체 합산 → 상세/첨부 → 통합 메일 1통 → 상태 기록 → 경보 판단

핵심 원칙: 한 기관이 실패해도 나머지 기관은 계속 진행한다.
KB 가 막혔다고 우리금융 공고까지 못 받는 일이 없어야 한다.
"""
from __future__ import annotations

import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.adapters import get_adapter
from core.fetcher import Fetcher
from core.mailer import Mailer
from core.models import Notice, RunResult, SourceResult
from core.render import render_alert, render_heartbeat, render_notices
from core.settings import Settings
from core.store import Store

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.store = Store(settings.data_dir / "state.sqlite3")
        self.mailer = Mailer(settings.smtp, settings.mail.subject_prefix,
                             settings.mail.delivery_mode)

    # ---------- 기관 1곳 ----------
    def _build(self, spec):
        fetcher = Fetcher(spec.base, delay=spec.request_delay,
                          timeout=spec.timeout, warm_path=spec.warm_path,
                          memo_path=self.s.data_dir / "strategy.json")
        return get_adapter(spec, fetcher), fetcher

    def scan_source(self, spec) -> SourceResult:
        res = SourceResult(source_id=spec.id, source_name=spec.name)
        adapter, fetcher = self._build(spec)
        try:
            notices, total = adapter.collect()
            res.scanned = len(notices)
            res.total = total
            res.strategy = fetcher.last_strategy
            res.parser = adapter.parser_used
            res.ok = True
            res.new_notices = notices  # 신규 판별은 호출부에서
            log.info("[%s] 조회 %d건 (전략=%s, 파서=%s)",
                     spec.id, res.scanned, res.strategy, res.parser)
        except Exception as exc:  # noqa: BLE001
            res.error = f"{type(exc).__name__}: {exc}"
            res.strategy = fetcher.last_strategy
            log.error("[%s] 조회 실패: %s", spec.id, res.error)
        return res, adapter  # type: ignore[return-value]

    def _snapshot(self, spec, html_text: str) -> None:
        if not self.s.runtime.keep_html_snapshot:
            return
        p = self.s.data_dir / f"snapshot_{spec.id}_{datetime.now():%Y%m%d_%H%M%S}.html"
        p.write_text(html_text, encoding="utf-8")
        log.error("파싱 실패 원본 HTML 보존: %s", p)

    # ---------- 첨부 ----------
    def fetch_attachments(self, by_adapter: List[Tuple[object, List[Notice]]]) -> Tuple[List[Path], Dict[str, set]]:
        if not self.s.mail.attach_pdf:
            return [], {}
        tmp = self.s.data_dir / "attach_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)

        budget = self.s.mail.max_attach_mb * 1024 * 1024
        paths: List[Path] = []
        index: Dict[str, set] = {}
        for adapter, notices in by_adapter:
            if not adapter.supports_attachment():  # type: ignore[attr-defined]
                continue
            for n in notices:
                for a in n.attachments:
                    if budget <= 0:
                        log.warning("첨부 용량 상한 도달 — 이후 첨부는 링크로만 안내")
                        return paths, index
                    try:
                        data = adapter.download(a)  # type: ignore[attr-defined]
                    except Exception as exc:  # noqa: BLE001
                        log.warning("첨부 다운로드 실패 %s: %s", a.display_name, exc)
                        continue
                    if len(data) > budget:
                        log.warning("첨부 %s 용량 초과 — 건너뜀", a.display_name)
                        continue
                    safe = f"{n.source_id}_{n.seq}_{a.display_name}"
                    safe = safe.replace("/", "_").replace("\\", "_")
                    p = tmp / safe
                    p.write_bytes(data)
                    paths.append(p)
                    index.setdefault(n.key, set()).add(a.display_name)
                    budget -= len(data)
        return paths, index

    # ---------- 경보 ----------
    def _alert(self, reason: str, detail: str) -> None:
        try:
            subj, html_body, text = render_alert(reason, detail)
            self.mailer.send(self.s.mail.alerts, subj, html_body, text)
            log.info("경보 메일 발송 완료")
        except Exception as exc:  # noqa: BLE001
            log.error("경보 메일마저 실패: %s", exc)

    def check_staleness(self) -> None:
        last = self.store.last_success_at()
        if last is None:
            return
        if datetime.now() - last > timedelta(hours=self.s.runtime.stale_hours_alert):
            self._alert(
                "마지막 정상 조회 이후 시간이 초과되었습니다",
                f"마지막 성공: {last:%Y-%m-%d %H:%M}\n"
                f"기준: {self.s.runtime.stale_hours_alert}시간\n"
                f"스케줄러가 멈췄거나 조회가 계속 실패하고 있을 수 있습니다.",
            )

    # ---------- 실행 ----------
    def run(self, seed: bool = False, force_mail: bool = False) -> RunResult:
        started = datetime.now()
        run = RunResult()
        specs = self.s.active_sources
        if not specs:
            run.error = "config.yaml 의 sources 가 비어 있습니다."
            log.error(run.error)
            return run

        fresh_all: List[Notice] = []
        by_adapter: List[Tuple[object, List[Notice]]] = []
        status: List[Tuple[str, bool, int, Optional[int]]] = []

        for spec in specs:
            first_run = self.store.is_empty(spec.id)
            res, adapter = self.scan_source(spec)
            run.sources.append(res)
            status.append((spec.name, res.ok, res.scanned, res.total))

            if not res.ok:
                self.store.log_run(started, False, spec.id, res.strategy, res.parser,
                                   0, 0, False, res.error)
                if self.store.consecutive_failures(spec.id) >= self.s.runtime.consecutive_failure_alert \
                        and self.s.runtime.alert_on_source_failure:
                    self._alert(
                        f"[{spec.name}] 조회가 연속 실패하고 있습니다",
                        f"{res.error}\n\n대상: {spec.base}\n"
                        f"자동 조회가 막혔거나 게시판 구조가 바뀌었을 수 있습니다.",
                    )
                continue

            scanned = res.new_notices
            known = self.store.known_keys(spec.id)
            fresh = [n for n in scanned if n.key not in known and self.s.filter.accepts(n.title)]
            fresh.sort(key=lambda n: (n.posted_at, n.seq))

            # 신규 기관은 과거 공고를 쏟아내지 않도록 기준선만 잡는다.
            if (first_run and not force_mail) or seed:
                self.store.remember(scanned, mailed=True)
                res.new_notices = []
                log.info("[%s] 기준선 등록: %d건을 '이미 본 공고'로 기록", spec.id, len(scanned))
                self.store.log_run(started, True, spec.id, res.strategy, res.parser,
                                   res.scanned, 0, False, None)
                continue

            # 지난 실행에서 발송에 실패해 남은 건을 다시 합류시킨다.
            # 이게 없으면 한 번 발송 실패한 공고는 영영 못 보낸다
            # (이미 seen_notice 에 있어 '신규'로 잡히지 않기 때문).
            pending = list(fresh)
            have = {n.key for n in pending}
            for r in self.store.unmailed_rows(spec.id):
                key = f"{r['source_id']}:{r['article_id']}"
                if key in have:
                    continue
                pending.append(Notice(
                    source_id=r["source_id"], source_name=spec.name,
                    seq=r["seq"] or 0, article_id=r["article_id"],
                    title=r["title"], posted_at=date.fromisoformat(r["posted_at"]),
                    url=r["url"],
                ))
                have.add(key)
            if len(pending) > len(fresh):
                log.warning("[%s] 이전 미발송 %d건을 재발송 대상에 포함합니다.",
                            spec.id, len(pending) - len(fresh))

            res.new_notices = pending
            if pending:
                adapter.enrich(pending)  # type: ignore[attr-defined]
                by_adapter.append((adapter, pending))
                fresh_all.extend(pending)
            # 신규가 아닌 것은 즉시 '발송완료'로, 신규는 아래에서 '미발송'으로 기록
            self.store.remember([n for n in scanned if n not in fresh], mailed=True)
            self.store.log_run(started, True, spec.id, res.strategy, res.parser,
                               res.scanned, len(pending), False, None)

        failed = [s.source_name for s in run.failed_sources]
        run.ok = any(s.ok for s in run.sources)

        if seed:
            log.info("기준선 등록 모드 — 메일을 보내지 않습니다.")
            return run

        if not fresh_all:
            if self._should_heartbeat():
                subj, html_body, text = render_heartbeat(status)
                try:
                    self.mailer.send(self.s.mail.alerts, subj, html_body, text)
                    run.mailed = True
                except Exception as exc:  # noqa: BLE001
                    log.error("생존 보고 발송 실패: %s", exc)
                    run.ok = False
                    run.error = str(exc)
            log.info("신규 공고 없음 (조회 %d건 / %d개 기관)", run.scanned, len(specs))
            self.check_staleness()
            return run

        log.info("신규 공고 %d건 감지 (%d개 기관)", len(fresh_all),
                 len({n.source_id for n in fresh_all}))

        # 발송 전에 '미발송'으로 먼저 기록 → 도중에 죽어도 다음 실행에서 재시도된다.
        self.store.remember(fresh_all, mailed=False)
        paths, index = self.fetch_attachments(by_adapter)
        subj, html_body, text = render_notices(fresh_all, index, failed)
        try:
            self.mailer.send(self.s.mail.recipients, subj, html_body, text, paths)
            self.store.mark_mailed(fresh_all)
            run.mailed = True
        except Exception as exc:  # noqa: BLE001
            run.ok = False
            run.error = f"메일 발송 실패: {exc}"
            log.error(run.error)
            self.store.log_run(started, False, None, None, None,
                               run.scanned, len(fresh_all), False, run.error)
        return run

    def _should_heartbeat(self) -> bool:
        if self.s.mail.send_when_empty:
            return True
        wd = self.s.mail.heartbeat_weekday
        return wd >= 0 and datetime.now().weekday() == wd
