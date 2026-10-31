"""Memoria persistente del bot (SQLite): chi è passato e messaggi lasciati per gli utenti."""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
CREATE TABLE IF NOT EXISTS karma (
    scope    TEXT NOT NULL,          -- canale (minuscolo) o '' se il karma è globale
    nick_key TEXT NOT NULL,
    nick     TEXT NOT NULL,
    mask     TEXT NOT NULL DEFAULT '',
    score    INTEGER NOT NULL DEFAULT 0,
    plus     INTEGER NOT NULL DEFAULT 0,
    minus    INTEGER NOT NULL DEFAULT 0,
    resets   INTEGER NOT NULL DEFAULT 0,
    updated  REAL NOT NULL,
    PRIMARY KEY (scope, nick_key)
);
CREATE TABLE IF NOT EXISTS karma_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL,
    giver_key    TEXT NOT NULL,
    receiver_key TEXT NOT NULL,
    delta        INTEGER NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    ts           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS karma_log_giver ON karma_log (giver_key, ts);
CREATE TABLE IF NOT EXISTS state (
    key     TEXT PRIMARY KEY,       -- es. "schedule:karma-mezzanotte", "youtube:@the-linux-nerd"
    value   TEXT NOT NULL,          -- JSON
    updated REAL NOT NULL
);
"""


class DatabaseLocked(RuntimeError):
    """Un'altra istanza del bot sta già usando questo database."""


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
class Karma:
    nick: str
    mask: str
    score: int
    plus: int
    minus: int
    resets: int
    updated: float


_KARMA_COLS = "nick, mask, score, plus, minus, resets, updated"


@dataclass
class Memo:
    id: int
    recipient: str
    sender: str
    channel: str
    text: str
    ts: float


def _timed(method):
    """Misura la durata delle operazioni sul database (per le statistiche e gli avvisi di lentezza)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return method(self, *args, **kwargs)
        finally:
            if self.on_timing is not None:
                self.on_timing(method.__name__, time.perf_counter() - started)
    return wrapper


class Memory:
    def __init__(self, path: str | Path, clock=time.time, lock: bool = False):
        self.path = str(path) if str(path) == ":memory:" else str(Path(path).expanduser().resolve())
        self.clock = clock
        self.on_timing = None
        self._lock_fh = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            if lock:
                self._acquire_lock()
        self.db = sqlite3.connect(self.path)
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")  # ogni scrittura è su disco anche se il PC si spegne di colpo
        self.db.executescript(SCHEMA)
        self.db.commit()

    def _acquire_lock(self) -> None:
        """Impedisce che due copie del bot usino lo stesso database (Linux e macOS)."""
        try:
            import fcntl
        except ImportError:  # Windows: nessun blocco
            return
        fh = open(self.path + ".lock", "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            other = fh.read().strip() or "?"
            fh.close()
            raise DatabaseLocked(f"il database {self.path} è già in uso da un'altra copia del bot (PID {other})") from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._lock_fh = fh

    def close(self) -> None:
        self.db.close()
        if self._lock_fh is not None:
            self._lock_fh.close()
            self._lock_fh = None

    # ------------------------------------------------------------------ stato generico

    @_timed
    def state_get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @_timed
    def state_set(self, key: str, value: Any) -> None:
        self.db.execute("INSERT INTO state (key, value, updated) VALUES (?, ?, ?) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                        (key, json.dumps(value), self.clock()))
        self.db.commit()

    @_timed
    def summary(self) -> dict[str, int]:
        """Quanti dati ci sono: utile nel log all'avvio per vedere che la memoria è stata ritrovata."""
        q = lambda sql: int(self.db.execute(sql).fetchone()[0])  # noqa: E731
        return {
            "utenti visti": q("SELECT COUNT(DISTINCT nick_key) FROM seen"),
            "messaggi in attesa": q("SELECT COUNT(*) FROM memos WHERE delivered_ts IS NULL"),
            "ban attivi": q("SELECT COUNT(*) FROM bans WHERE removed_ts IS NULL"),
            "utenti con karma": q("SELECT COUNT(*) FROM karma WHERE score != 0"),
        }

    @_timed
    def backup(self, directory: str | Path, keep: int = 7) -> Path:
        """Copia coerente del database (anche mentre il bot scrive) in ``directory``; tiene le ultime ``keep``."""
        folder = Path(directory).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(self.path).stem if self.path != ":memory:" else "memoria"
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(self.clock()))
        target = folder / f"{stem}-{stamp}.db"
        n = 1
        while target.exists():  # due copie nello stesso secondo: non sovrascrivere
            target = folder / f"{stem}-{stamp}-{n}.db"
            n += 1
        dest = sqlite3.connect(str(target))
        try:
            self.db.backup(dest)
        finally:
            dest.close()
        if keep > 0:
            old = sorted(folder.glob(f"{stem}-*.db"))[:-keep]
            for path in old:
                path.unlink(missing_ok=True)
        return target

    # ------------------------------------------------------------------ seen

    @_timed
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

    @_timed
    def last_seen(self, nick: str) -> SeenEntry | None:
        row = self.db.execute(
            "SELECT nick, channel, mask, event, detail, ts, joins FROM seen WHERE nick_key = ? "
            "ORDER BY ts DESC LIMIT 1", (irc_lower(nick),)).fetchone()
        return SeenEntry(*row) if row else None

    @_timed
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

    @_timed
    def prune(self, retention_days: float) -> None:
        cutoff = self.clock() - retention_days * 86400
        self.db.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
        self.db.execute("DELETE FROM memos WHERE delivered_ts IS NOT NULL AND delivered_ts < ?", (cutoff,))
        self.db.execute("DELETE FROM bans WHERE removed_ts IS NOT NULL AND removed_ts < ?", (cutoff,))
        self.db.execute("DELETE FROM karma_log WHERE ts < ?", (cutoff,))  # i punteggi restano
        self.db.commit()

    # ------------------------------------------------------------------ memo

    @_timed
    def add_memo(self, sender: str, recipient: str, text: str, channel: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO memos (recipient_key, recipient, sender, channel, text, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (irc_lower(recipient), recipient, sender, channel, text, self.clock()))
        self.db.commit()
        return int(cur.lastrowid)

    @_timed
    def pending_count(self, recipient: str, sender: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM memos WHERE recipient_key = ? AND delivered_ts IS NULL"
        params = [irc_lower(recipient)]
        if sender is not None:
            sql += " AND sender = ?"
            params.append(sender)
        return int(self.db.execute(sql, params).fetchone()[0])

    @_timed
    def pending(self, recipient: str, expire_days: float = 0) -> list[Memo]:
        sql = ("SELECT id, recipient, sender, channel, text, ts FROM memos "
               "WHERE recipient_key = ? AND delivered_ts IS NULL")
        params: list = [irc_lower(recipient)]
        if expire_days:
            sql += " AND ts >= ?"
            params.append(self.clock() - expire_days * 86400)
        return [Memo(*r) for r in self.db.execute(sql + " ORDER BY ts", params)]

    @_timed
    def mark_delivered(self, memos: list[Memo]) -> None:
        now = self.clock()
        self.db.executemany("UPDATE memos SET delivered_ts = ? WHERE id = ?", [(now, m.id) for m in memos])
        self.db.commit()


    # ------------------------------------------------------------------ ban

    @_timed
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

    @_timed
    def get_ban(self, ban_id: int) -> Ban | None:
        row = self.db.execute(f"SELECT {_BAN_COLS} FROM bans WHERE id = ?", (ban_id,)).fetchone()
        return Ban(*row) if row else None

    @_timed
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

    @_timed
    def expired_bans(self) -> list[Ban]:
        rows = self.db.execute(f"SELECT {_BAN_COLS} FROM bans WHERE removed_ts IS NULL AND expires IS NOT NULL "
                               "AND expires <= ? ORDER BY expires", (self.clock(),))
        return [Ban(*r) for r in rows]

    @_timed
    def remove_ban(self, ban_id: int, by: str = "") -> None:
        self.db.execute("UPDATE bans SET removed_ts = ?, removed_by = ? WHERE id = ? AND removed_ts IS NULL",
                        (self.clock(), by, ban_id))
        self.db.commit()


    # ------------------------------------------------------------------ karma

    @_timed
    def karma_get(self, scope: str, nick: str) -> Karma | None:
        row = self.db.execute(f"SELECT {_KARMA_COLS} FROM karma WHERE scope = ? AND nick_key = ?",
                              (irc_lower(scope), irc_lower(nick))).fetchone()
        return Karma(*row) if row else None

    @_timed
    def karma_change(self, scope: str, nick: str, delta: int, giver: str, mask: str = "",
                     reason: str = "") -> Karma:
        scope, key, now = irc_lower(scope), irc_lower(nick), self.clock()
        self.db.execute(
            """INSERT INTO karma (scope, nick_key, nick, mask, score, plus, minus, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (scope, nick_key) DO UPDATE SET
                 nick = excluded.nick,
                 mask = CASE WHEN excluded.mask != '' THEN excluded.mask ELSE karma.mask END,
                 score = karma.score + excluded.score, plus = karma.plus + excluded.plus,
                 minus = karma.minus + excluded.minus, updated = excluded.updated""",
            (scope, key, nick, mask, delta, max(delta, 0), max(-delta, 0), now))
        self.db.execute("INSERT INTO karma_log (scope, giver_key, receiver_key, delta, reason, ts) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (scope, irc_lower(giver), key, delta, reason, now))
        self.db.commit()
        return self.karma_get(scope, nick)  # type: ignore[return-value]

    @_timed
    def karma_reset(self, scope: str, nick: str, by: str, reason: str = "reset") -> int | None:
        """Azzera il punteggio; restituisce quello precedente (None se non c'era)."""
        current = self.karma_get(scope, nick)
        if current is None:
            return None
        scope, key = irc_lower(scope), irc_lower(nick)
        self.db.execute("UPDATE karma SET score = 0, resets = resets + 1, updated = ? WHERE scope = ? AND "
                        "nick_key = ?", (self.clock(), scope, key))
        self.db.execute("INSERT INTO karma_log (scope, giver_key, receiver_key, delta, reason, ts) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (scope, irc_lower(by), key, -current.score, reason, self.clock()))
        self.db.commit()
        return current.score

    @_timed
    def karma_top(self, scope: str, limit: int, worst: bool = False) -> list[Karma]:
        order = "ASC" if worst else "DESC"
        rows = self.db.execute(f"SELECT {_KARMA_COLS} FROM karma WHERE scope = ? AND score != 0 "
                               f"ORDER BY score {order}, updated ASC LIMIT ?", (irc_lower(scope), limit))
        return [Karma(*r) for r in rows]

    @_timed
    def karma_rank(self, scope: str, nick: str) -> tuple[int, int]:
        """(posizione, totale) fra chi ha karma diverso da zero; posizione 0 se non in classifica."""
        scope = irc_lower(scope)
        me = self.karma_get(scope, nick)
        total = self.db.execute("SELECT COUNT(*) FROM karma WHERE scope = ? AND score != 0", (scope,)).fetchone()[0]
        if me is None or me.score == 0:
            return 0, int(total)
        above = self.db.execute("SELECT COUNT(*) FROM karma WHERE scope = ? AND score > ?",
                                (scope, me.score)).fetchone()[0]
        return int(above) + 1, int(total)

    @_timed
    def karma_all(self, scope: str) -> list[Karma]:
        return [Karma(*r) for r in self.db.execute(f"SELECT {_KARMA_COLS} FROM karma WHERE scope = ?",
                                                   (irc_lower(scope),))]

    @_timed
    def karma_last_given(self, scope: str, giver: str, receiver: str) -> float | None:
        row = self.db.execute("SELECT MAX(ts) FROM karma_log WHERE scope = ? AND giver_key = ? AND receiver_key = ? "
                              "AND reason = 'utente'", (irc_lower(scope), irc_lower(giver), irc_lower(receiver))).fetchone()
        return row[0] if row and row[0] is not None else None

    @_timed
    def karma_given_since(self, giver: str, since: float) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM karma_log WHERE giver_key = ? AND ts >= ? AND reason = 'utente'",
                                   (irc_lower(giver), since)).fetchone()[0])


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
