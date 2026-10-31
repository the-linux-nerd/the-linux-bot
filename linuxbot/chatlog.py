"""Log delle conversazioni per canale, un file al giorno: ``<cartella>/<canale>/AAAA-MM-GG.log``."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import IO, Callable

from .config import ChatLogConfig
from .irc import irc_lower

log = logging.getLogger("linuxbot.channels")

PRIVATE_DIR = "_privati"


def _safe(name: str) -> str:
    name = irc_lower(name).lstrip("#&+!") or "canale"
    return re.sub(r"[^\w.\-]", "_", name)


class ChatLog:
    def __init__(self, cfg: ChatLogConfig, directory: Path, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.directory = directory
        self.clock = clock
        self.files: dict[str, tuple[str, IO[str]]] = {}  # chiave canale -> (data, file)
        self._last_prune = ""
        self.channels = {irc_lower(c) for c in cfg.channels}

    def wanted(self, channel: str) -> bool:
        if not channel:
            return self.cfg.private
        return not self.channels or irc_lower(channel) in self.channels

    def write(self, channel: str, text: str) -> None:
        """Scrive una riga nel log di ``channel`` ("" = messaggi privati)."""
        if not self.wanted(channel):
            return
        now = datetime.fromtimestamp(self.clock())
        day = now.strftime("%Y-%m-%d")
        key = irc_lower(channel) if channel else PRIVATE_DIR
        current = self.files.get(key)
        try:
            if current is None or current[0] != day:
                if current is not None:
                    current[1].close()
                folder = self.directory / (_safe(channel) if channel else PRIVATE_DIR)
                folder.mkdir(parents=True, exist_ok=True)
                fh = open(folder / f"{day}.log", "a", encoding="utf-8")
                self.files[key] = (day, fh)
                current = self.files[key]
                if self._last_prune != day:
                    self._last_prune = day
                    self.prune()
            current[1].write(f"[{now.strftime('%H:%M:%S')}] {text}\n")
            current[1].flush()
        except OSError as exc:
            log.error("Impossibile scrivere il log della chat di %s: %s", channel or "privati", exc)

    def prune(self) -> None:
        """Cancella i file più vecchi di ``retention_days``."""
        if self.cfg.retention_days <= 0 or not self.directory.exists():
            return
        cutoff = datetime.fromtimestamp(self.clock() - self.cfg.retention_days * 86400).strftime("%Y-%m-%d")
        for path in self.directory.glob("*/*.log"):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem) and path.stem < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def close(self) -> None:
        for _, fh in self.files.values():
            fh.close()
        self.files.clear()
