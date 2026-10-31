"""Metriche di funzionamento del bot, mostrate con l'azione ``stats`` (comando ``!stats``)."""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from .memory import format_duration


@dataclass
class Timing:
    count: int = 0
    total: float = 0.0
    max: float = 0.0
    max_name: str = ""
    slow: int = 0

    def add(self, seconds: float, name: str = "", slow: bool = False) -> None:
        self.count += 1
        self.total += seconds
        if seconds > self.max:
            self.max, self.max_name = seconds, name
        if slow:
            self.slow += 1

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0


@dataclass
class Stats:
    clock: callable = time.monotonic  # type: ignore[valid-type]
    started: float = 0.0
    connected_at: float | None = None
    connections: int = 0
    disconnects: int = 0
    last_disconnect: str = ""
    lines_in: int = 0
    lines_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    commands: Counter = field(default_factory=Counter)
    responses: Counter = field(default_factory=Counter)
    command_time: Timing = field(default_factory=Timing)
    action_time: Timing = field(default_factory=Timing)
    line_time: Timing = field(default_factory=Timing)
    db_time: Timing = field(default_factory=Timing)
    flood_delayed: int = 0
    flood_wait: float = 0.0
    queue_max: int = 0
    warnings: int = 0
    errors: int = 0
    last_errors: deque = field(default_factory=lambda: deque(maxlen=5))

    def __post_init__(self) -> None:
        self.started = self.clock()

    # ------------------------------------------------------------------ registrazione

    def connected(self) -> None:
        self.connections += 1
        self.connected_at = self.clock()

    def disconnected(self, reason: str) -> None:
        if self.connected_at is not None:
            self.disconnects += 1
        self.connected_at = None
        self.last_disconnect = reason

    def log_event(self, level: int, area: str, message: str) -> None:
        if level >= logging.ERROR:
            self.errors += 1
            self.last_errors.append((time.time(), area, message[:200]))
        elif level >= logging.WARNING:
            self.warnings += 1

    # ------------------------------------------------------------------ testo

    @staticmethod
    def _ms(seconds: float) -> str:
        ms = seconds * 1000
        return f"{ms:.1f} ms" if ms < 100 else f"{ms:.0f} ms"

    def report(self, server: str, queue_now: int, section: str = "") -> list[str]:
        now = self.clock()
        lines = []
        if section in ("", "generale", "general"):
            conn = (f"connesso da {format_duration(now - self.connected_at)} a {server}"
                    if self.connected_at is not None else "non connesso")
            last = f", ultima: {self.last_disconnect}" if self.last_disconnect else ""
            lines.append(f"Attivo da {format_duration(now - self.started)}, {conn} "
                         f"(connessioni: {self.connections}, cadute: {self.disconnects}{last}). "
                         f"Righe ricevute {self.lines_in} ({self.bytes_in // 1024} KB), "
                         f"inviate {self.lines_out} ({self.bytes_out // 1024} KB).")
        if section in ("", "comandi", "commands"):
            top = ", ".join(f"{n} {c}" for n, c in self.commands.most_common(8)) or "nessuno"
            resp = ", ".join(f"{n} {c}" for n, c in self.responses.most_common(5)) or "nessuna"
            ct = self.command_time
            slowest = f" ({ct.max_name})" if ct.max_name else ""
            lines.append(f"Comandi: {sum(self.commands.values())} ({top}); tempo medio {self._ms(ct.avg)}, "
                         f"massimo {self._ms(ct.max)}{slowest}. Risposte automatiche: "
                         f"{sum(self.responses.values())} ({resp}).")
            lt, at = self.line_time, self.action_time
            lines.append(f"Elaborazione righe: media {self._ms(lt.avg)}, massimo {self._ms(lt.max)}, "
                         f"lente {lt.slow}. Azioni: {at.count}, media {self._ms(at.avg)}, "
                         f"massimo {self._ms(at.max)}{f' ({at.max_name})' if at.max_name else ''}, lente {at.slow}.")
        if section in ("", "flood"):
            lines.append(f"Anti-flood: {self.flood_delayed} messaggi ritardati, attesa totale "
                         f"{self.flood_wait:.1f} s, coda massima {self.queue_max} (ora {queue_now}).")
        if section in ("", "db", "database"):
            db = self.db_time
            lines.append(f"Database: {db.count} operazioni, media {self._ms(db.avg)}, massimo "
                         f"{self._ms(db.max)}{f' ({db.max_name})' if db.max_name else ''}, lente {db.slow}.")
        if section in ("", "errori", "errors"):
            text = f"Log: {self.errors} errori, {self.warnings} avvisi."
            if self.last_errors:
                ts, area, msg = self.last_errors[-1]
                text += f" Ultimo errore ({time.strftime('%d/%m %H:%M', time.localtime(ts))}, {area}): {msg}"
            lines.append(text)
        return lines
