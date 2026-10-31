"""Memoria persistente del bot (SQLite): chi è passato e messaggi lasciati per gli utenti."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .irc import irc_lower

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    nick_key  TEXT NOT NULL,
    channel   TEXT NOT NULL,
    nick      TEXT NOT NULL,
    mask      TEXT NOT NULL DEFAULT '',
    event     TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    ts        REAL NOT NULL,
    joins     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (nick_key, channel)
);
CREATE INDEX IF NOT EXISTS seen_ts ON seen (ts);
CREATE TABLE IF NOT EXISTS memos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_key TEXT NOT NULL,
    recipient     TEXT NOT NULL,
    sender        TEXT NOT NULL,
    channel       TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL,
    ts            REAL NOT NULL,
    delivered_ts  REAL
);
CREATE INDEX IF NOT EXISTS memos_pending ON memos (recipient_key, delivered_ts);
"""


@dataclass
class SeenEntry:
    nick: str
    channel: str
    mask: str
    event: str
    detail: str
    ts: float
    joins: int


@dataclass
class Memo:
    id: int
    recipient: str
    sender: str
    channel: str
    text: str
    ts: float


class Memory:
    def __init__(self, path: str | Path, clock=time.time):
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ seen

    def record(self, nick: str, channel: str, event: str, detail: str = "", mask: str = "") -> None:
        """Registra l'ultima attività di ``nick`` in ``channel``. ``event``: join, part, quit, kick, nick, message."""
        key, chan = irc_lower(nick), irc_lower(channel)
        joined = 1 if event == "join" else 0
        self.db.execute(
            """INSERT INTO seen (nick_key, channel, nick, mask, event, detail, ts, joins)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (nick_key, channel) DO UPDATE SET
                 nick = excluded.nick,
                 mask = CASE WHEN excluded.mask != '' THEN excluded.mask ELSE seen.mask END,
                 event = excluded.event, detail = excluded.detail, ts = excluded.ts,
                 joins = seen.joins + excluded.joins""",
            (key, chan, nick, mask, event, detail[:300], self.clock(), joined),
        )
        self.db.commit()

    def last_seen(self, nick: str) -> SeenEntry | None:
        row = self.db.execute(
            "SELECT nick, channel, mask, event, detail, ts, joins FROM seen WHERE nick_key = ? "
            "ORDER BY ts DESC LIMIT 1", (irc_lower(nick),)).fetchone()
        return SeenEntry(*row) if row else None

    def recent(self, seconds: float, channel: str | None = None, exclude: set[str] = frozenset()) -> list[SeenEntry]:
        """Utenti visti negli ultimi ``seconds`` secondi, dal più recente; un'occorrenza per nick."""
        cutoff = self.clock() - seconds
        sql = "SELECT nick, channel, mask, event, detail, MAX(ts), SUM(joins) FROM seen WHERE ts >= ?"
        params: list = [cutoff]
        if channel:
            sql += " AND channel = ?"
            params.append(irc_lower(channel))
        sql += " GROUP BY nick_key ORDER BY MAX(ts) DESC"
        rows = [SeenEntry(*r) for r in self.db.execute(sql, params)]
        return [r for r in rows if irc_lower(r.nick) not in exclude]

    def prune(self, retention_days: float) -> None:
        cutoff = self.clock() - retention_days * 86400
        self.db.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
        self.db.execute("DELETE FROM memos WHERE delivered_ts IS NOT NULL AND delivered_ts < ?", (cutoff,))
        self.db.commit()

    # ------------------------------------------------------------------ memo

    def add_memo(self, sender: str, recipient: str, text: str, channel: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO memos (recipient_key, recipient, sender, channel, text, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (irc_lower(recipient), recipient, sender, channel, text, self.clock()))
        self.db.commit()
        return int(cur.lastrowid)

    def pending_count(self, recipient: str, sender: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM memos WHERE recipient_key = ? AND delivered_ts IS NULL"
        params = [irc_lower(recipient)]
        if sender is not None:
            sql += " AND sender = ?"
            params.append(sender)
        return int(self.db.execute(sql, params).fetchone()[0])

    def pending(self, recipient: str, expire_days: float = 0) -> list[Memo]:
        sql = ("SELECT id, recipient, sender, channel, text, ts FROM memos "
               "WHERE recipient_key = ? AND delivered_ts IS NULL")
        params: list = [irc_lower(recipient)]
        if expire_days:
            sql += " AND ts >= ?"
            params.append(self.clock() - expire_days * 86400)
        return [Memo(*r) for r in self.db.execute(sql + " ORDER BY ts", params)]

    def mark_delivered(self, memos: list[Memo]) -> None:
        now = self.clock()
        self.db.executemany("UPDATE memos SET delivered_ts = ? WHERE id = ?", [(now, m.id) for m in memos])
        self.db.commit()


def time_ago(ts: float, now: float | None = None) -> str:
    """Tempo trascorso in italiano: "3 giorni fa", "un'ora fa", "pochi secondi fa"."""
    delta = max(0, (now if now is not None else time.time()) - ts)
    units = [(86400 * 365, "anno", "anni", "un"), (86400 * 30, "mese", "mesi", "un"),
             (86400 * 7, "settimana", "settimane", "una"), (86400, "giorno", "giorni", "un"),
             (3600, "ora", "ore", "un'"), (60, "minuto", "minuti", "un")]
    for size, one, many, article in units:
        n = int(delta // size)
        if n >= 1:
            if n == 1:
                return f"{article}{one} fa" if article.endswith("'") else f"{article} {one} fa"
            return f"{n} {many} fa"
    return "pochi secondi fa"


def format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
