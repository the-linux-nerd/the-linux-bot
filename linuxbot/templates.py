"""Rendering dei testi configurati con variabili ``{nick}``, ``{channel}``, ecc."""

from __future__ import annotations

import random
import re
from urllib.parse import quote, quote_plus
from typing import Any, Mapping

# Solo segnaposto semplici: {nome} o {nome|filtro|filtro}. Niente attributi o indici, così il
# testo scritto dagli utenti (che finisce nei valori, mai nel template) è innocuo.
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)((?:\|[a-z]+)*)\}")

FILTERS = {
    "url": lambda v: quote_plus(v),               # parametro di ricerca: spazi -> +
    "path": lambda v: quote(v, safe=""),          # pezzo di percorso: spazi -> %20, / codificato
    "lower": str.lower,
    "upper": str.upper,
    "strip": str.strip,
    "title": str.title,
}


def render(template: str, variables: Mapping[str, Any]) -> str:
    """Sostituisce i segnaposto; quelli sconosciuti restano invariati. ``{{``/``}}`` sono letterali.

    Filtri: ``{args|url}`` (per i link di ricerca), ``{arg1|path}``, ``|lower``, ``|upper``, ``|strip``, ``|title``.
    """

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        name, filters = match.group(1), [f for f in match.group(2).split("|") if f]
        if name not in variables or variables[name] is None or any(f not in FILTERS for f in filters):
            return token
        value = str(variables[name])
        for f in filters:
            value = FILTERS[f](value)
        return value

    return _PLACEHOLDER.sub(repl, str(template))


def pick(value: Any) -> str | None:
    """Una lista significa "scegline uno a caso"; una stringa viene usata così com'è."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return str(random.choice(value)) if value else None
    return str(value)


def args_variables(args: list[str], nick: str) -> dict[str, str]:
    """Variabili derivate dagli argomenti di un comando."""
    variables = {
        "args": " ".join(args),
        "argc": str(len(args)),
        "rest": " ".join(args[1:]),
        "rest2": " ".join(args[2:]),
        "target": args[0] if args else nick,
    }
    for i in range(1, 10):
        variables[f"arg{i}"] = args[i - 1] if i <= len(args) else ""
    return variables
