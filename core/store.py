"""상태 저장 계층(SQLite).

재실행 안전성·중복 발송 방지·'조용한 실패' 탐지의 단일 근거 데이터.

다중 기관을 다루므로 중복 판정 키는 (source_id, article_id) 조합이다.
기관이 다르면 공고번호가 겹칠 수 있어 article_id 단독으로는 안 된다.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS seen_notice (
    source_id  TEXT NOT NULL DEFAULT 'kb',
    article_id TEXT NOT NULL,
    seq        INTEGER,
    title      TEXT,
    posted_at  TEXT,
    url        TEXT,
    found_at   TEXT,
    mailed_at  TEXT,
    PRIMARY KEY (source_id, article_id)
);
CREATE TABLE IF NOT EXISTS run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    ok         INTEGER,
    source_id  TEXT,
    strategy   TEXT,
    parser     TEXT,
    scanned    INTEGER,
    new_count  INTEGER,
    mailed     INTEGER,
    error      TEXT
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(self.path)) as con:
            self._migrate(con)
            con.executescript(DDL)
            con.commit()

    # ------------------------------------------------------------------
    def _migrate(self, con: sqlite3.Connection) -> None:
        """구버전(단일 기관) 스키마를 다중 기관 스키마로 승격한다.

        기존 데이터를 버리면 이미 보낸 공고가 신규로 잡혀 재발송되므로
        반드시 보존한 채 옮긴다. 기존 행은 전부 KB 로 간주한다.
        """
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "seen_notice" in tables:
            cols = {r[1] for r in con.execute("PRAGMA table_info(seen_notice)")}
            if "source_id" not in cols:
                log.warning("구버전 상태 파일 감지 — 다중 기관 스키마로 이관합니다.")
                con.executescript("""
                    ALTER TABLE seen_notice RENAME TO seen_notice_old;
                    CREATE TABLE seen_notice (
                        source_id  TEXT NOT NULL DEFAULT 'kb',
                        article_id TEXT NOT NULL,
                        seq        INTEGER, title TEXT, posted_at TEXT,
                        url TEXT, found_at TEXT, mailed_at TEXT,
                        PRIMARY KEY (source_id, article_id)
                    );
                    INSERT OR IGNORE INTO seen_notice
                        (source_id, article_id, seq, title, posted_at, url, found_at, mailed_at)
                    SELECT 'kb', article_id, seq, title, posted_at, url, found_at, mailed_at
                    FROM seen_notice_old;
                    DROP TABLE seen_notice_old;
                """)
                n = con.execute("SELECT COUNT(*) FROM seen_notice").fetchone()[0]
                log.warning("이관 완료: 기존 %d건을 'kb' 기관으로 보존했습니다.", n)

        if "run_log" in tables:
            cols = {r[1] for r in con.execute("PRAGMA table_info(run_log)")}
            if "source_id" not in cols:
                con.execute("ALTER TABLE run_log ADD COLUMN source_id TEXT")
        con.commit()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    # ---------- 공고 ----------
    def known_keys(self, source_id: Optional[str] = None) -> set:
        """'source_id:article_id' 형태의 키 집합."""
        q = "SELECT source_id, article_id FROM seen_notice"
        args: tuple = ()
        if source_id:
            q += " WHERE source_id=?"
            args = (source_id,)
        with closing(self._con()) as con:
            return {f"{r[0]}:{r[1]}" for r in con.execute(q, args)}

    def remember(self, notices: Iterable, mailed: bool) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (n.source_id, n.article_id, n.seq, n.title, n.posted_at.isoformat(),
             n.url, now, now if mailed else None)
            for n in notices
        ]
        if not rows:
            return
        with closing(self._con()) as con:
            con.executemany(
                "INSERT OR IGNORE INTO seen_notice"
                " (source_id, article_id, seq, title, posted_at, url, found_at, mailed_at)"
                " VALUES (?,?,?,?,?,?,?,?)", rows)
            con.commit()

    def mark_mailed(self, notices: Iterable) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(now, n.source_id, n.article_id) for n in notices]
        if not rows:
            return
        with closing(self._con()) as con:
            con.executemany(
                "UPDATE seen_notice SET mailed_at=?"
                " WHERE source_id=? AND article_id=? AND mailed_at IS NULL", rows)
            con.commit()

    def unmailed_rows(self, source_id: Optional[str] = None) -> List[sqlite3.Row]:
        """발송에 실패해 아직 못 보낸 공고들.

        known_keys 에는 이미 들어 있어 '신규'로 잡히지 않으므로,
        이 목록을 별도로 꺼내 발송 대상에 다시 합쳐야 재시도가 성립한다.
        """
        q = ("SELECT source_id, article_id, seq, title, posted_at, url"
             " FROM seen_notice WHERE mailed_at IS NULL")
        args: tuple = ()
        if source_id:
            q += " AND source_id=?"
            args = (source_id,)
        q += " ORDER BY posted_at, seq"
        with closing(self._con()) as con:
            return list(con.execute(q, args))

    def unmailed_count(self) -> int:
        with closing(self._con()) as con:
            return con.execute(
                "SELECT COUNT(*) FROM seen_notice WHERE mailed_at IS NULL").fetchone()[0]

    def is_empty(self, source_id: Optional[str] = None) -> bool:
        q = "SELECT COUNT(*) FROM seen_notice"
        args: tuple = ()
        if source_id:
            q += " WHERE source_id=?"
            args = (source_id,)
        with closing(self._con()) as con:
            return con.execute(q, args).fetchone()[0] == 0

    def counts_by_source(self) -> List[sqlite3.Row]:
        with closing(self._con()) as con:
            return list(con.execute(
                "SELECT source_id, COUNT(*) AS n, MAX(seq) AS max_seq"
                " FROM seen_notice GROUP BY source_id ORDER BY source_id"))

    # ---------- 실행 이력 ----------
    def log_run(self, started: datetime, ok: bool, source_id: Optional[str],
                strategy: Optional[str], parser: Optional[str], scanned: int,
                new_count: int, mailed: bool, error: Optional[str]) -> None:
        with closing(self._con()) as con:
            con.execute(
                "INSERT INTO run_log"
                " (started_at, ok, source_id, strategy, parser, scanned, new_count, mailed, error)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (started.isoformat(timespec="seconds"), int(ok), source_id, strategy,
                 parser, scanned, new_count, int(mailed), (error or "")[:2000]))
            con.commit()

    def consecutive_failures(self, source_id: Optional[str] = None) -> int:
        q = "SELECT ok FROM run_log"
        args: tuple = ()
        if source_id:
            q += " WHERE source_id=?"
            args = (source_id,)
        q += " ORDER BY id DESC LIMIT 20"
        with closing(self._con()) as con:
            n = 0
            for r in con.execute(q, args):
                if r[0]:
                    break
                n += 1
            return n

    def last_success_at(self) -> Optional[datetime]:
        with closing(self._con()) as con:
            r = con.execute(
                "SELECT started_at FROM run_log WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
            return datetime.fromisoformat(r[0]) if r else None
