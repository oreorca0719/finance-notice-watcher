"""이미 발송한 공고를 다시 보내는 도구.

서식을 바꿨을 때 새 형식으로 다시 돌려보거나, 수신자를 추가한 뒤
최근 공고를 새 담당자에게 공유할 때 쓴다.

  python resend.py --days 1                 # 최근 1일 이내 등록 공고 재발송
  python resend.py --days 3 --dry-run       # 발송 없이 대상만 확인
  python resend.py --seq kb:4918,hana:1528508   # 특정 공고만 지정 발송
  python resend.py --source hanati,kbfg --days 30   # 특정 기관만 (신규 기관 검증용)

**상태 파일을 건드리지 않는다.** 재발송해도 '발송 완료' 이력은 그대로이므로
다음 정기 실행의 신규 판정에 영향을 주지 않는다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.mailer import Mailer  # noqa: E402
from core.render import render_notices  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.watcher import Watcher  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="이미 발송한 공고 재발송")
    ap.add_argument("--days", type=int, default=1,
                    help="오늘 기준 N일 이내에 '등록된' 공고를 대상으로 한다 (기본 1)")
    ap.add_argument("--seq", type=str, default="",
                    help="특정 공고만: 'kb:4918,hana:1528508' 형식")
    ap.add_argument("--source", type=str, default="",
                    help="기관 id 만 골라서: 'hanati,kbfg' 형식. 생략하면 전체 기관")
    ap.add_argument("--dry-run", action="store_true", help="발송하지 않고 대상만 출력")
    ap.add_argument("--to", type=str, default="",
                    help="수신자 직접 지정(쉼표 구분). 생략하면 recipients 전원")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    log = logging.getLogger("resend")

    s = Settings.load()
    w = Watcher(s)

    wanted = set()
    for tok in (t.strip() for t in args.seq.split(",") if t.strip()):
        sid, _, num = tok.partition(":")
        if num.isdigit():
            wanted.add((sid, int(num)))

    only = {x.strip() for x in args.source.split(",") if x.strip()}
    if only:
        known = {spec.id for spec in s.active_sources}
        unknown = only - known
        if unknown:
            log.warning("--source 에 없는 기관 id: %s (사용 가능: %s)",
                        ", ".join(sorted(unknown)), ", ".join(sorted(known)))

    cutoff = date.today() - timedelta(days=args.days)
    picked = []
    for spec in s.active_sources:
        if only and spec.id not in only:
            continue
        # 특정 공고 지정 시 해당 기관만 조회해 불필요한 요청을 줄인다.
        if wanted and not any(sid == spec.id for sid, _ in wanted):
            continue
        res, _ = w.scan_source(spec)
        if not res.ok:
            log.warning("[%s] 조회 실패: %s", spec.name, res.error)
            continue
        for n in res.new_notices:
            if not s.filter.accepts(n.title, spec.skip_require):
                continue
            if wanted:
                if (spec.id, n.seq) in wanted:
                    picked.append(n)
            elif n.posted_at >= cutoff:
                picked.append(n)

    if not picked:
        log.info("대상 공고가 없습니다.")
        return 0

    picked.sort(key=lambda n: (n.source_name, -n.seq))
    log.info("재발송 대상 %d건", len(picked))
    for n in picked:
        log.info("   [%s] #%s %s (%s)", n.source_name, n.seq, n.title[:52], n.posted_at)

    to = [x.strip() for x in args.to.split(",") if x.strip()] or s.mail.recipients
    if args.dry_run:
        log.info("--dry-run 이므로 발송하지 않습니다. 수신 예정: %d명 %s", len(to), ", ".join(to))
        return 0

    subj, html_body, text = render_notices(picked)
    m = Mailer(s.smtp, s.mail.subject_prefix, s.mail.delivery_mode)
    m.send(to, subj, html_body, text)
    log.info("재발송 완료 → %d명: %s", len(to), ", ".join(to))
    log.info("상태 파일은 변경하지 않았습니다. 정기 실행의 신규 판정에 영향 없습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
