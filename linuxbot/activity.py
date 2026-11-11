"""Mappa dell'attività dei canali: disegno in ASCII e import dello storico dai log.

Il bot conta i messaggi di canale per giorno e per ora (tabella ``activity`` del database);
qui ci sono il disegno della heatmap giorni × ore e la lettura dei log già esistenti
(weechat e la chatlog del bot), per riempire la mappa anche col passato.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from .irc import irc_lower

DEFAULT_SCALE = "·░▒▓█"
DEFAULT_SLOT = 4
SLOTS = (1, 2, 3, 4, 6, 8, 12)

_NICK = r"[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]*"
# weechat: "2024-05-16 21:03:11<TAB>@nick<TAB>testo"; le righe di servizio hanno "-->", "<--", "--", " *"
_WEECHAT = re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}}) (\d{{2}}):\d{{2}}:\d{{2}}\t[@%+~&!]*({_NICK})\t")
# chatlog del bot: "<canale>/AAAA-MM-GG.log" con righe "[HH:MM:SS] <nick> testo"
_CHATLOG = re.compile(rf"^\[(\d{{2}}):\d{{2}}:\d{{2}}\] <({_NICK})> ")
_CHATLOG_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEECHAT_NAME = re.compile(r"(#[^/]+?)\.weechatlog$")


def level(value: int, peak: int, scale: str) -> str:
    """Il carattere della scala per ``value``: il primo solo per lo zero, l'ultimo per il picco."""
    if value <= 0 or peak <= 0:
        return scale[0]
    steps = len(scale) - 1
    return scale[max(1, min(steps, math.ceil(value * steps / peak)))]


def slots(grid: list[list[int]], slot: int = DEFAULT_SLOT) -> list[list[int]]:
    """Raggruppa le 24 ore di ogni giorno in fasce di ``slot`` ore."""
    return [[sum(row[h:h + slot]) for h in range(0, 24, slot)] for row in grid]


def render_map(grid: list[list[int]], day_names: list[str], hour_label: str = "ore",
               scale: str = DEFAULT_SCALE, slot: int = DEFAULT_SLOT) -> list[str]:
    """Disegna la griglia 7×24 (lunedì per primo) a fasce di ``slot`` ore: l'intestazione con le
    fasce, poi una riga per giorno col totale.

    Ogni fascia è una colonna larga quanto la sua etichetta (``20-24``): tre caratteri della scala
    pieni quanto i messaggi, oppure, se è vuota, il primo carattere della scala in mezzo."""
    if len(scale) < 2:
        scale = DEFAULT_SCALE
    if slot not in SLOTS:
        slot = DEFAULT_SLOT
    cells = slots(grid, slot)
    peak = max((v for row in cells for v in row), default=0)
    width = max([len(hour_label)] + [len(n) for n in day_names])

    def cell(v: int) -> str:
        return f"  {scale[0]}  " if v <= 0 else f" {level(v, peak, scale) * 3} "

    lines = [hour_label.ljust(width) + " " + " ".join(f"{h:02d}-{h + slot:02d}" for h in range(0, 24, slot))]
    for name, row in zip(day_names, cells):
        lines.append(f"{name.ljust(width)} {' '.join(map(cell, row))}  {sum(row)}")
    return lines


def best_slots(grid: list[list[int]], slot: int = DEFAULT_SLOT, n: int = 3) -> list[tuple[int, int, int]]:
    """Le ``n`` fasce con più messaggi, come (giorno, ora d'inizio, messaggi): le più vive prima."""
    if slot not in SLOTS:
        slot = DEFAULT_SLOT
    found = [(v, d, i * slot) for d, row in enumerate(slots(grid, slot)) for i, v in enumerate(row) if v > 0]
    found.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [(d, h, v) for v, d, h in found[:n]]


def _channel_of(path: Path, channel: str | None) -> str | None:
    if channel:
        return channel
    if _CHATLOG_NAME.match(path.stem):
        return "#" + path.parent.name
    m = _WEECHAT_NAME.search(path.name)
    return m.group(1) if m else None


def count_log(path: str | Path, channel: str | None = None,
              exclude: set[str] = frozenset()) -> tuple[str | None, Counter]:
    """Conta i messaggi di un log per (giorno, ora): restituisce il canale e il conteggio.

    Riconosce il formato dal nome: ``AAAA-MM-GG.log`` è la chatlog del bot (il canale è la cartella),
    il resto è un log di weechat (``irc.rete.#canale.weechatlog``). Le azioni /me, gli ingressi, le uscite
    e i messaggi dei nick in ``exclude`` (in minuscolo IRC) non si contano, come dal vivo."""
    path = Path(path)
    counts: Counter = Counter()
    chatlog_day = path.stem if _CHATLOG_NAME.match(path.stem) else None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if chatlog_day:
                m = _CHATLOG.match(line)
                if m and irc_lower(m.group(2)) not in exclude:
                    counts[(chatlog_day, int(m.group(1)))] += 1
            else:
                m = _WEECHAT.match(line)
                if m and irc_lower(m.group(3)) not in exclude:
                    counts[(m.group(1), int(m.group(2)))] += 1
    return _channel_of(path, channel), counts


def log_files(paths: list[str | Path]) -> list[Path]:
    """I file da importare: quelli indicati, e per le cartelle tutti i log dentro (non quelli privati)."""
    out: list[Path] = []
    for p in map(lambda x: Path(x).expanduser(), paths):
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in (".log", ".weechatlog")
                          and "_privati" not in f.parts)
        else:
            out.append(p)
    return out
