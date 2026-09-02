"""SMTP 발송 계층.

발송 자체도 실패할 수 있으므로 재시도하고, 최종 실패 시 예외를 올려
상위가 '미발송'으로 기록하게 한다. 미발송 건은 다음 실행에서 자동 재시도된다.
"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)


class MailError(RuntimeError):
    pass


class Mailer:
    """다중 수신자 발송.

    delivery_mode
      bcc        : 1회 전송, 수신자는 서로의 주소를 볼 수 없음(기본)
      individual : 수신자별 개별 전송 — 한 명이 실패해도 나머지는 정상 수신
      to         : 1회 전송, 수신자 전원이 서로의 주소를 봄

    bcc/to 로 일괄 전송하다 실패하면 자동으로 individual 로 강등해 재시도한다.
    한 명의 잘못된 주소 때문에 전원이 못 받는 사태를 막기 위해서다.
    """

    def __init__(self, smtp_cfg, subject_prefix: str = "", delivery_mode: str = "bcc") -> None:
        self.cfg = smtp_cfg
        self.prefix = subject_prefix
        self.mode = (delivery_mode or "bcc").lower()
        self.last_failed: List[str] = []

    def _build(
        self,
        to: Sequence[str],
        subject: str,
        html_body: str,
        text_body: str,
        attachments: Optional[List[Path]] = None,
        header_mode: Optional[str] = None,
    ) -> EmailMessage:
        msg = EmailMessage()
        subj = f"{self.prefix} {subject}".strip()
        # EmailMessage 는 기본 정책에서 유니코드를 스스로 인코딩한다.
        # 구형 Header 객체를 넣으면 TypeError 가 나므로 순수 문자열로 넘긴다.
        msg["Subject"] = subj
        msg["From"] = formataddr((self.cfg.from_name, self.cfg.sender))
        mode = header_mode or self.mode
        if mode == "bcc":
            # To 에는 발신자 자신을 넣고 실제 수신자는 envelope(RCPT TO)로만 전달한다.
            msg["To"] = self.cfg.sender
        else:
            msg["To"] = ", ".join(to)
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(text_body or "본문 없음")
        msg.add_alternative(html_body, subtype="html")

        for p in attachments or []:
            if not p.exists():
                continue
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(
                p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name
            )
        return msg

    def send(
        self,
        to: Sequence[str],
        subject: str,
        html_body: str,
        text_body: str,
        attachments: Optional[List[Path]] = None,
    ) -> None:
        if not self.cfg.configured:
            raise MailError(
                "SMTP 설정이 비어 있습니다. .env 의 SMTP_HOST/SMTP_USER/SMTP_PASSWORD 를 채우십시오."
            )
        if not to:
            raise MailError("수신자가 없습니다. config.yaml 의 mail.recipients 를 확인하십시오.")

        if self.mode == "individual":
            self._send_individually(to, subject, html_body, text_body, attachments)
            return

        # 1차: 일괄 전송(bcc 또는 to)
        msg = self._build(to, subject, html_body, text_body, attachments)
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                self._transmit(msg, to)
                log.info("메일 발송 성공(%s, %d명): %s", self.mode, len(to), subject)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("일괄 발송 실패(%d/3): %s", attempt + 1, exc)
                time.sleep(3 * (attempt + 1))

        # 2차: 첨부가 원인일 수 있으므로 첨부 없이 재시도.
        # 메일 서버의 용량 한도를 넘기면 발송이 영원히 실패해 그 공고를 못 받게 된다.
        # 첨부를 포기하더라도 본문의 원문 링크는 살아 있으므로 알림 자체는 전달된다.
        if attachments and self._looks_like_size_error(last):
            log.warning("첨부 용량 문제로 보입니다. 첨부 없이 재발송합니다. (원인: %s)", last)
            try:
                bare = self._build(to, subject, html_body,
                                   text_body + "\n\n※ 첨부파일이 용량 제한으로 제외되었습니다. "
                                               "본문의 링크에서 내려받으십시오.", None)
                self._transmit(bare, to)
                log.info("첨부 제외 후 발송 성공: %s", subject)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("첨부 제외 재발송도 실패: %s", exc)

        # 3차: 개별 전송으로 강등 — 문제 주소 1건이 전원 발송을 막지 못하게 한다.
        log.warning("일괄 발송이 계속 실패하여 수신자별 개별 발송으로 전환합니다. (원인: %s)", last)
        self._send_individually(to, subject, html_body, text_body, attachments)

    @staticmethod
    def _looks_like_size_error(exc: Optional[Exception]) -> bool:
        if exc is None:
            return False
        t = f"{type(exc).__name__} {exc}".lower()
        return any(k in t for k in (
            "size", "too large", "exceed", "limit", "552", "523", "quota", "message too big"))

    def _send_individually(
        self,
        to: Sequence[str],
        subject: str,
        html_body: str,
        text_body: str,
        attachments: Optional[List[Path]] = None,
    ) -> None:
        ok: List[str] = []
        failed: List[str] = []
        for addr in to:
            msg = self._build([addr], subject, html_body, text_body, attachments, header_mode="to")
            sent = False
            for attempt in range(2):
                try:
                    self._transmit(msg, [addr])
                    sent = True
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning("개별 발송 실패 %s (%d/2): %s", addr, attempt + 1, exc)
                    time.sleep(2)
            (ok if sent else failed).append(addr)

        log.info("개별 발송 결과: 성공 %d명 / 실패 %d명", len(ok), len(failed))
        if not ok:
            raise MailError(f"전원 발송 실패: {', '.join(failed)}")
        if failed:
            # 일부 실패는 발송 자체를 실패로 보지 않되, 로그와 상위 경보로 드러낸다.
            log.error("일부 수신자 발송 실패: %s", ", ".join(failed))
            self.last_failed = failed

    @staticmethod
    def _contexts() -> List[ssl.SSLContext]:
        """엄격한 TLS 컨텍스트 → 완화된 컨텍스트 순으로 시도한다.

        후이즈웍스(smtp.whoisworks.com) 같은 일부 국내 메일 서버는 Diffie-Hellman
        파라미터가 1024비트라 Python 3.10+ 기본 보안수준(SECLEVEL=2)에서
        'DH_KEY_TOO_SMALL' 로 협상이 거부된다. 이 경우 SECLEVEL=1 로 낮춰
        연결한다. 평문 전송이 아니라 TLS 암호화는 그대로 유지된다.
        """
        strict = ssl.create_default_context()
        relaxed = ssl.create_default_context()
        try:
            relaxed.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:  # 빌드에 따라 미지원일 수 있음
            pass
        return [strict, relaxed]

    def _transmit(self, msg: EmailMessage, to: Sequence[str]) -> None:
        last: Optional[Exception] = None
        for idx, ctx in enumerate(self._contexts()):
            try:
                self._transmit_with(msg, to, ctx)
                if idx > 0:
                    log.warning(
                        "서버의 TLS 파라미터가 약해 보안수준을 완화해 연결했습니다"
                        " (TLS 암호화는 유지됨)."
                    )
                return
            except ssl.SSLError as exc:
                last = exc
                log.warning("TLS 협상 실패(%d차): %s", idx + 1, exc)
        raise MailError(f"TLS 협상 실패: {last}")

    def _transmit_with(self, msg: EmailMessage, to: Sequence[str], ctx: ssl.SSLContext) -> None:
        c = self.cfg
        if c.security.lower() == "ssl":
            with smtplib.SMTP_SSL(c.host, c.port, context=ctx, timeout=30) as s:
                s.login(c.user, c.password)
                # envelope 은 인증 계정 유지. 서버가 불일치를 거부하는 경우가 많다.
                s.send_message(msg, from_addr=c.user, to_addrs=list(to))
            return
        with smtplib.SMTP(c.host, c.port, timeout=30) as s:
            s.ehlo()
            if c.security.lower() == "starttls":
                s.starttls(context=ctx)
                s.ehlo()
            s.login(c.user, c.password)
            s.send_message(msg, from_addr=c.user, to_addrs=list(to))
