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

DEFAULT_SCALE = " .:-=+*#%@"

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


def render_map(grid: list[list[int]], day_names: list[str], hour_label: str = "ora",
               scale: str = DEFAULT_SCALE) -> list[str]:
    """Disegna la griglia 7×24 (lunedì per primo): una riga di ore, poi una riga per giorno col totale.

    Ogni ora occupa due caratteri, così le etichette ogni tre ore stanno allineate sopra le celle."""
    if len(scale) < 2:
        scale = DEFAULT_SCALE
    width = max([len(hour_label)] + [len(n) for n in day_names])
    peak = max((v for row in grid for v in row), default=0)
    header = hour_label.ljust(width) + " " + "".join(f"{h:02d}    " for h in range(0, 24, 3)).rstrip()
    lines = [header]
    for name, row in zip(day_names, grid):
        cells = "".join(level(v, peak, scale) * 2 for v in row)
        lines.append(f"{name.ljust(width)} {cells} {sum(row)}")
    return lines


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
