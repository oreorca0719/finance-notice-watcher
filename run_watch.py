"""엔트리포인트. Windows 작업 스케줄러와 GitHub Actions가 공통으로 이 파일을 호출한다.

  python run_watch.py            # 정상 실행(신규 공고 감지 → 메일)
  python run_watch.py --seed     # 기준선만 등록(현재 목록을 '이미 본 것'으로 표시)
  python run_watch.py --dry-run  # 메일 발송 없이 감지 결과만 출력
  python run_watch.py --test-mail  # SMTP 설정 점검용 테스트 메일 1통
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.settings import Settings  # noqa: E402
from core.watcher import Watcher  # noqa: E402


def setup_logging(settings: Settings, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fh = RotatingFileHandler(
        settings.log_dir / "watch.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def main() -> int:
    ap = argparse.ArgumentParser(description="금융권 프로젝트 공고 감시기 (다중 기관)")
    ap.add_argument("--seed", action="store_true", help="현재 목록을 기준선으로 등록하고 종료")
    ap.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 감지만 수행")
    ap.add_argument("--force-mail", action="store_true", help="최초 실행이어도 즉시 메일 발송")
    ap.add_argument("--test-mail", action="store_true", help="SMTP 점검용 테스트 메일 발송")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    settings = Settings.load()
    setup_logging(settings, args.verbose)
    log = logging.getLogger("run")

    if args.test_mail:
        from core.mailer import Mailer

        m = Mailer(settings.smtp, settings.mail.subject_prefix, settings.mail.delivery_mode)
        m.send(
            settings.mail.recipients,
            "발송 경로 점검",
            "<p>KB 공고 감시기 SMTP 설정이 정상입니다.</p>",
            "KB 공고 감시기 SMTP 설정이 정상입니다.",
        )
        log.info("테스트 메일 발송 완료 → %d명: %s", len(settings.mail.recipients), ", ".join(settings.mail.recipients))
        return 0

    watcher = Watcher(settings)

    if args.dry_run:
        total_new = 0
        for spec in settings.active_sources:
            res, _ = watcher.scan_source(spec)
            if not res.ok:
                log.error("[%s] 조회 실패: %s", spec.name, res.error)
                continue
            known = watcher.store.known_keys(spec.id)
            fresh = [n for n in res.new_notices
                     if n.key not in known and settings.filter.accepts(n.title)]
            total_new += len(fresh)
            log.info("[%s] 조회 %d건 / 전체 %s건 / 신규 %d건 (전략=%s, 파서=%s)",
                     spec.name, res.scanned, res.total, len(fresh), res.strategy, res.parser)
            for n in fresh:
                log.info("     신규: [%s] %s (%s)", n.seq, n.title, n.posted_at)
        log.info("합계 신규 %d건 / 기관 %d곳", total_new, len(settings.active_sources))
        return 0

    res = watcher.run(seed=args.seed, force_mail=args.force_mail)

    if not res.ok:
        log.error("실행 실패: %s", res.error)
        return 1
    log.info("실행 완료 | %s | 메일발송 %s",
             " / ".join(f"{k} {v}" for k, v in res.summary().items()), res.mailed)
    for sr in res.sources:
        log.info("   [%s] %s | 조회 %d건 | 신규 %d건 | 전략 %s",
                 sr.source_name, "정상" if sr.ok else "실패",
                 sr.scanned, len(sr.new_notices), sr.strategy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
