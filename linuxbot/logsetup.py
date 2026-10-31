"""Configurazione dei log: livelli per area, rotazione, JSON, password nascoste, avvisi agli admin.

Tutti i log del bot passano per il logger ``linuxbot`` e i suoi figli, uno per area
(``linuxbot.connection``, ``linuxbot.irc``, ``linuxbot.bans``...).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from .config import LOG_AREAS, Config

if TYPE_CHECKING:
    from .bot import Bot

ROOT = "linuxbot"
TEXT_FORMAT = "%(asctime)s %(levelname)-7s [%(area)s] %(message)s"

# Righe IRC con password: PASS, OPER, AUTHENTICATE e identificazione con i services.
# Se un'espressione ha dei gruppi, vengono nascosti solo quelli; altrimenti tutto il testo trovato.
_LINE = r"(?:^|[<>]> )"
BUILTIN_REDACT = [
    _LINE + r"PASS :?(\S+)",
    _LINE + r"OPER \S+ :?(\S+)",
    _LINE + r"AUTHENTICATE (?!PLAIN\b|\+$)(\S+)",
    r"(?i)serv :?(?:IDENTIFY|LOGIN|REGISTER|GHOST \S+|RECOVER \S+) (\S+(?: \S+)?)",
]


def area_logger(area: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{area}")


class AreaFilter(logging.Filter):
    """Aggiunge ``record.area`` (connection, irc, bans...) per il formato."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        record.area = name[len(ROOT) + 1:] if name.startswith(ROOT + ".") else "core"
        return True


class RedactFilter(logging.Filter):
    """Nasconde password e testi sensibili in ogni messaggio, qualunque sia la sua origine."""

    def __init__(self, patterns: list[str], secrets: list[str]):
        super().__init__()
        self.patterns = [re.compile(p) for p in patterns]
        literal = [re.escape(s) for s in sorted(set(secrets), key=len, reverse=True) if s and len(s) >= 3]
        if literal:
            self.patterns.append(re.compile("|".join(literal)))

    def redact(self, text: str) -> str:
        for rx in self.patterns:
            if rx.groups:
                def hide(m: re.Match[str]) -> str:
                    out, pos = [], m.start()
                    for i in range(1, rx.groups + 1):
                        if m.start(i) >= 0:
                            out.append(m.string[pos:m.start(i)] + "********")
                            pos = m.end(i)
                    return "".join(out) + m.string[pos:m.end()]
                text = rx.sub(hide, text)
            else:
                text = rx.sub("********", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.patterns:
            return True
        message = record.getMessage()
        clean = self.redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "area": getattr(record, "area", record.name),
            "msg": record.getMessage(),
        }
        if record.exc_info or record.exc_text:
            data["exc"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class StatsHandler(logging.Handler):
    """Conta avvisi ed errori per ``!stats``."""

    def __init__(self, bot: "Bot"):
        super().__init__(logging.WARNING)
        self.bot = bot

    def emit(self, record: logging.LogRecord) -> None:
        self.bot.stats.log_event(record.levelno, getattr(record, "area", ""), record.getMessage())


class AlertHandler(logging.Handler):
    """Manda gli avvisi gravi su IRC agli admin (in privato o in un canale staff), con limiti anti-spam."""

    def __init__(self, bot: "Bot", cfg):
        super().__init__(getattr(logging, cfg.level))
        self.bot, self.cfg = bot, cfg
        self.last_sent: dict[str, float] = {}
        self.sent_times: list[float] = []
        self.local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self.local, "busy", False) or record.name == f"{ROOT}.irc":
            return  # niente ricorsione e niente traffico grezzo
        bot = self.bot
        if not bot.registered or bot._queue is None:
            return
        now = time.monotonic()
        message = " ".join(record.getMessage().split())[:300]
        key = f"{record.name}:{message[:80]}"
        if now - self.last_sent.get(key, -1e9) < self.cfg.min_interval:
            return
        self.sent_times = [t for t in self.sent_times if now - t < 3600]
        if len(self.sent_times) >= self.cfg.max_per_hour:
            return
        self.last_sent[key] = now
        self.sent_times.append(now)
        label = {"WARNING": "AVVISO", "ERROR": "ERRORE", "CRITICAL": "CRITICO"}.get(record.levelname,
                                                                                   record.levelname)
        text = f"[{label}] {getattr(record, 'area', '')}: {message}"
        self.local.busy = True
        try:
            for target in self.cfg.to:
                (bot.notice if self.cfg.method == "notice" else bot.say)(target, text)
        finally:
            self.local.busy = False


def _secrets(config: Config) -> list[str]:
    auth = config.auth
    values = [config.server.password or "", auth.sasl.password, auth.nickserv.password, auth.oper.password]
    values += [ch.key or "" for ch in config.channels]
    return [v for v in values if v]


def configure_logging(config: Config, bot: "Bot | None" = None, verbose: bool = False) -> None:
    """(Ri)configura i log secondo ``config.logging``. Si può chiamare di nuovo dopo un reload."""
    lc = config.logging
    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        if getattr(handler, "_linuxbot", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel("DEBUG" if verbose else lc.level)
    root.propagate = False
    for area in LOG_AREAS:
        level = lc.levels.get(area)
        area_logger(area).setLevel(level if level and not verbose else logging.NOTSET)

    formatter: logging.Formatter = JsonFormatter() if lc.format == "json" else logging.Formatter(TEXT_FORMAT)
    filters: list[logging.Filter] = [AreaFilter(), RedactFilter(BUILTIN_REDACT + lc.redact, _secrets(config))]

    handlers: list[logging.Handler] = []
    if lc.console:
        handlers.append(logging.StreamHandler())
    if lc.file.path:
        path = config.resolve(lc.file.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if lc.file.rotate == "size":
            handlers.append(logging.handlers.RotatingFileHandler(
                path, maxBytes=lc.file.max_bytes, backupCount=lc.file.backups, encoding="utf-8"))
        elif lc.file.rotate == "daily":
            handlers.append(logging.handlers.TimedRotatingFileHandler(
                path, when="midnight", backupCount=lc.file.backups, encoding="utf-8"))
        else:
            handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
    if bot is not None:
        handlers.append(StatsHandler(bot))
        if lc.alerts.enabled:
            handlers.append(AlertHandler(bot, lc.alerts))
    for handler in handlers:
        for f in filters:
            handler.addFilter(f)
        handler._linuxbot = True  # type: ignore[attr-defined]
        root.addHandler(handler)
