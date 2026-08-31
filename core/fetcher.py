"""다중 전략 HTTP 수집 계층 (기관 무관 범용).

'어느 날 갑자기 조회가 막히면 안 된다'는 요구를 코드로 옮긴 부분이다.
전략을 순서대로 시도하고 어느 단계에서 성공했는지 기록한다.
전부 실패하면 예외를 올려 상위가 경보를 보내게 한다.

사이트별 URL 조립 지식은 여기 없다. 전부 어댑터가 갖는다.
"""
from __future__ import annotations

import json
import logging
import ssl
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 v1/v2 모두 지원
    from urllib3.util.ssl_ import create_urllib3_context
except Exception:  # noqa: BLE001
    create_urllib3_context = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

# OpenSSL 3.0 이 기본 차단하는 legacy renegotiation 을 허용하는 플래그.
# Python 3.12+ 에는 ssl.OP_LEGACY_SERVER_CONNECT 가 있으나 3.8 에는 없으므로 값을 직접 쓴다.
_OP_LEGACY_SERVER_CONNECT = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


class _LegacyTlsAdapter(HTTPAdapter):
    """구형 TLS 스택 서버 전용 어댑터.

    우리금융그룹(www.woorifg.com) 처럼 OpenSSL 3.0 기준으로는 거부되는 서버가 있다.
    이 어댑터는 legacy renegotiation 을 허용하고 보안수준을 1로 낮춰 접속만 성사시킨다.
    평문이 아니라 TLS 는 그대로 유지되며, 기본 경로가 실패했을 때만 쓰인다.
    """

    def init_poolmanager(self, *args, **kwargs):  # noqa: D102
        if create_urllib3_context is not None:
            ctx = create_urllib3_context()
            ctx.options |= _OP_LEGACY_SERVER_CONNECT
            try:
                ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            except ssl.SSLError:
                pass
            kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, base: str, delay: float = 0.8, timeout: int = 30,
                 warm_path: str = "/", memo_path: Optional[Path] = None) -> None:
        self.base = base.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.warm_path = warm_path
        self.last_strategy: Optional[str] = None
        self._session: Optional[requests.Session] = None
        self._ua_idx = 0
        self._legacy: Optional[requests.Session] = None
        # 지난번에 성공한 전략을 기억해 먼저 시도한다.
        # 이게 없으면 매 실행마다 실패할 전략 4개를 먼저 돌려 10초씩 낭비한다.
        self._memo_path = memo_path
        self._preferred: Optional[str] = self._load_memo()

    # ---------- 전략 기억 ----------
    def _load_memo(self) -> Optional[str]:
        if not self._memo_path or not self._memo_path.exists():
            return None
        try:
            return json.loads(self._memo_path.read_text(encoding="utf-8")).get(self.base)
        except Exception:  # noqa: BLE001
            return None

    def _save_memo(self, strategy: str) -> None:
        if not self._memo_path or strategy == self._preferred:
            return
        try:
            data = {}
            if self._memo_path.exists():
                data = json.loads(self._memo_path.read_text(encoding="utf-8"))
            data[self.base] = strategy
            self._memo_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
            self._preferred = strategy
        except Exception as exc:  # noqa: BLE001
            log.warning("전략 기억 저장 실패: %s", exc)

    # ---------- 세션 ----------
    def _new_session(self, ua: str) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Referer": self.base + "/",
            "Connection": "keep-alive",
        })
        return s

    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self._new_session(UA_POOL[0])
        return self._session

    def rotate(self) -> requests.Session:
        self._ua_idx = (self._ua_idx + 1) % len(UA_POOL)
        self._session = self._new_session(UA_POOL[self._ua_idx])
        return self._session

    def legacy_session(self) -> requests.Session:
        """구형 TLS 서버 전용 세션. 한 번 성사되면 이후 요청에도 계속 쓴다."""
        if self._legacy is None:
            s = self._new_session(UA_POOL[0])
            s.mount("https://", _LegacyTlsAdapter())
            self._legacy = s
        return self._legacy

    # ---------- 전략 ----------
    def _plain(self, url: str) -> str:
        r = self.session().get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding if r.encoding is None else "utf-8"
        return r.text

    def _warmed(self, url: str) -> str:
        """메인 페이지를 먼저 방문해 쿠키를 확보한 뒤 조회. 세션 검증형 차단 대응."""
        s = self.rotate()
        try:
            s.get(self.base + self.warm_path, timeout=self.timeout)
            time.sleep(0.4)
        except Exception:  # noqa: BLE001
            pass
        r = s.get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text

    def _legacy_tls(self, url: str) -> str:
        r = self.legacy_session().get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text

    def _browser(self, url: str) -> str:
        """최후 수단: 실제 브라우저 엔진. JS 렌더링·WAF 챌린지 대응."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        for a in ("--headless=new", "--disable-gpu", "--no-sandbox",
                  "--window-size=1400,1000", "--lang=ko-KR", "--log-level=3"):
            opts.add_argument(a)
        opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
        drv = webdriver.Chrome(options=opts)
        try:
            drv.set_page_load_timeout(self.timeout)
            drv.get(url)
            time.sleep(2.5)
            return drv.page_source
        finally:
            drv.quit()

    def _plan(self, url: str, alt: Optional[str]) -> List[Tuple[str, Callable[[], str]]]:
        plan: List[Tuple[str, Callable[[], str]]] = [
            ("http-plain", lambda: self._plain(url)),
            ("http-retry", lambda: self._plain(url)),
            ("http-warmed", lambda: self._warmed(url)),
        ]
        if alt:
            plan.append(("http-alt-url", lambda: self._warmed(alt)))
        # 구형 TLS 서버 대응. 브라우저 폴백보다 앞에 둔다 —
        # 브라우저는 Chrome/selenium 설치가 필요해 서버 환경에서 못 쓸 수 있다.
        plan.append(("http-legacy-tls", lambda: self._legacy_tls(url)))
        plan.append(("browser-headless", lambda: self._browser(url)))

        # 지난번 성공 전략을 맨 앞으로 끌어온다. 실패하면 나머지가 순서대로 이어 시도된다.
        if self._preferred:
            plan.sort(key=lambda kv: 0 if kv[0] == self._preferred else 1)
        return plan

    # ---------- 공개 API ----------
    def get(self, url: str, alt_url: Optional[str] = None,
            validator: Optional[Callable[[str], bool]] = None) -> str:
        errors: List[str] = []
        for name, fn in self._plan(url, alt_url):
            try:
                html_text = fn()
                if validator and not validator(html_text):
                    raise FetchError("응답은 받았으나 기대한 게시판 구조가 아님")
                self.last_strategy = name
                if name != self._preferred and name != "http-plain":
                    log.warning("'%s' 전략으로 수집 성공", name)
                self._save_memo(name)
                time.sleep(self.delay)
                return html_text
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__} {exc}")
                log.warning("전략 '%s' 실패: %s", name, exc)
                time.sleep(1.2)
        raise FetchError("모든 수집 전략 실패\n" + "\n".join(errors))

    def get_bytes(self, url: str) -> bytes:
        last: Optional[Exception] = None
        for i in range(3):
            try:
                sess = self.legacy_session() if i else self.session()
                r = sess.get(url, timeout=self.timeout)
                r.raise_for_status()
                time.sleep(self.delay)
                return r.content
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.5)
        raise FetchError(f"파일 다운로드 실패: {last}")

    def post(self, url: str, form: dict) -> bytes:
        last: Optional[Exception] = None
        for i in range(3):
            try:
                sess = self.legacy_session() if i else self.session()
                r = sess.post(url, data=form, timeout=self.timeout)
                r.raise_for_status()
                time.sleep(self.delay)
                return r.content
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.5)
        raise FetchError(f"첨부 다운로드 실패: {last}")
