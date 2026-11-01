#!/usr/bin/env python3
"""Rigenera le parti di docs/riferimento.md che vengono dal codice (i testi predefiniti).

Uso: python contrib/gen-docs.py   (dalla cartella del progetto)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from linuxbot.config import ABOUT_TEXTS, BAN_TEXTS, KARMA_TEXTS, MEMORY_TEXTS, SEARCH_TEXTS  # noqa: E402

SECTIONS = [("memory.texts", MEMORY_TEXTS), ("bans.texts", BAN_TEXTS), ("karma.texts", KARMA_TEXTS),
            ("search.texts", SEARCH_TEXTS), ("about.texts", ABOUT_TEXTS)]


def texts_tables() -> str:
    out = []
    for section, texts in SECTIONS:
        out.append(f"\n### `{section}`\n\n| Chiave | Testo predefinito |\n|---|---|")
        for key, value in texts.items():
            cell = value.replace("|", "\\|").replace("\n", " ")
            out.append(f"| `{key}` | {cell} |")
    return "\n".join(out) + "\n"


def main() -> int:
    path = ROOT / "docs" / "riferimento.md"
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"(<!-- TESTI:INIZIO -->\n).*?(<!-- TESTI:FINE -->)",
                 lambda m: m.group(1) + texts_tables() + m.group(2), text, flags=re.S)
    if "--check" in sys.argv:
        if new != text:
            print("docs/riferimento.md non è aggiornato: esegui python contrib/gen-docs.py", file=sys.stderr)
            return 1
        return 0
    path.write_text(new, encoding="utf-8")
    print("docs/riferimento.md aggiornato")
    return 0


if __name__ == "__main__":
    sys.exit(main())
