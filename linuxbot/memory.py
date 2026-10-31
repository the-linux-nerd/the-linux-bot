"""Memoria persistente del bot (SQLite): chi è passato e messaggi lasciati per gli utenti."""

from __future__ import annotations

import re
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
CREATE TABLE IF NOT EXISTS bans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key TEXT NOT NULL,
    channel     TEXT NOT NULL,
    mask        TEXT NOT NULL,
    mask_key    TEXT NOT NULL,
    nick        TEXT NOT NULL DEFAULT '',
    nick_key    TEXT NOT NULL DEFAULT '',
    setter      TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    ts          REAL NOT NULL,
    expires     REAL,
    manual      INTEGER NOT NULL DEFAULT 0,
    removed_ts  REAL,
    removed_by  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS bans_active ON bans (channel_key, removed_ts);
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
class Ban:
    id: int
    channel: str
    mask: str
    nick: str
    setter: str
    reason: str
    ts: float
    expires: float | None
    manual: bool
    removed_ts: float | None
    removed_by: str

    @property
    def active(self) -> bool:
        return self.removed_ts is None


_BAN_COLS = "id, channel, mask, nick, setter, reason, ts, expires, manual, removed_ts, removed_by"


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
        self.db.execute("DELETE FROM bans WHERE removed_ts IS NOT NULL AND removed_ts < ?", (cutoff,))
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


    # ------------------------------------------------------------------ ban

    def add_ban(self, channel: str, mask: str, nick: str = "", setter: str = "", reason: str = "",
                expires: float | None = None, manual: bool = False, ts: float | None = None) -> Ban:
        """Registra un ban attivo; se esiste già per lo stesso canale e maschera lo aggiorna."""
        existing = self.find_bans(channel, mask=mask)
        if existing:
            ban = existing[0]
            self.db.execute(
                "UPDATE bans SET nick = CASE WHEN ? != '' THEN ? ELSE nick END, setter = ?, reason = ?, "
                "expires = ?, manual = ? WHERE id = ?",
                (nick, nick, setter or ban.setter, reason or ban.reason, expires, int(manual and ban.manual), ban.id))
            self.db.commit()
            return self.get_ban(ban.id)  # type: ignore[return-value]
        cur = self.db.execute(
            "INSERT INTO bans (channel_key, channel, mask, mask_key, nick, nick_key, setter, reason, ts, expires, "
            "manual) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (irc_lower(channel), channel, mask, irc_lower(mask), nick, irc_lower(nick), setter, reason,
             ts if ts is not None else self.clock(), expires, int(manual)))
        self.db.commit()
        return self.get_ban(int(cur.lastrowid))  # type: ignore[return-value]

    def get_ban(self, ban_id: int) -> Ban | None:
        row = self.db.execute(f"SELECT {_BAN_COLS} FROM bans WHERE id = ?", (ban_id,)).fetchone()
        return Ban(*row) if row else None

    def find_bans(self, channel: str | None = None, mask: str | None = None, nick: str | None = None,
                  active: bool = True) -> list[Ban]:
        sql = f"SELECT {_BAN_COLS} FROM bans WHERE 1=1"
        params: list = []
        if active:
            sql += " AND removed_ts IS NULL"
        if channel:
            sql += " AND channel_key = ?"
            params.append(irc_lower(channel))
        if mask is not None:
            sql += " AND mask_key = ?"
            params.append(irc_lower(mask))
        if nick is not None:
            sql += " AND nick_key = ?"
            params.append(irc_lower(nick))
        sql += " ORDER BY ts DESC" if not active else " ORDER BY ts"
        return [Ban(*r) for r in self.db.execute(sql, params)]

    def expired_bans(self) -> list[Ban]:
        rows = self.db.execute(f"SELECT {_BAN_COLS} FROM bans WHERE removed_ts IS NULL AND expires IS NOT NULL "
                               "AND expires <= ? ORDER BY expires", (self.clock(),))
        return [Ban(*r) for r in rows]

    def remove_ban(self, ban_id: int, by: str = "") -> None:
        self.db.execute("UPDATE bans SET removed_ts = ?, removed_by = ? WHERE id = ? AND removed_ts IS NULL",
                        (self.clock(), by, ban_id))
        self.db.commit()


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "o": 3600, "d": 86400, "g": 86400, "w": 604800, "M": 2592000}
_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([smhodgwM]?)")


def parse_duration(text: str) -> float | None:
    """``30m``, ``2h``, ``1d12h``, ``1w``, ``90`` (minuti). Restituisce secondi, 0 = permanente, None = non valido.

    Unità: s secondi, m minuti, h/o ore, d/g giorni, w settimane, M mesi (30 giorni).
    """
    text = (text or "").strip()
    if text.lower() in ("0", "perm", "permanente", "per sempre", "forever"):
        return 0.0
    if not text:
        return None
    pos, total = 0, 0.0
    for m in _DURATION_RE.finditer(text):
        if m.start() != pos:
            return None
        total += float(m.group(1).replace(",", ".")) * _DURATION_UNITS[m.group(2) or "m"]
        pos = m.end()
    if pos != len(text) or total <= 0:
        return None
    return total


def format_duration(seconds: float) -> str:
    """Durata leggibile in italiano: "2 ore", "1 giorno e 3 ore", "30 minuti"."""
    seconds = int(round(seconds))
    parts = []
    for size, one, many in [(604800, "settimana", "settimane"), (86400, "giorno", "giorni"),
                            (3600, "ora", "ore"), (60, "minuto", "minuti")]:
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return "meno di un minuto"
    parts = parts[:2]
    return " e ".join(parts)


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
