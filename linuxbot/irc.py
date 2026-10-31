"""Primitive del protocollo IRC: parsing dei messaggi, maschere, casemapping."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

_RFC1459 = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\~", "abcdefghijklmnopqrstuvwxyz{}|^")


def irc_lower(text: str) -> str:
    """Minuscolo secondo il casemapping rfc1459 (il default della maggior parte dei server)."""
    return text.translate(_RFC1459)


def is_channel(name: str) -> bool:
    return bool(name) and name[0] in "#&+!"


def mask_match(mask: str, hostmask: str) -> bool:
    """Confronta una maschera stile IRC (``*!*@*.example.org``) con ``nick!user@host``."""
    pattern = fnmatch.translate(irc_lower(mask))
    return re.match(pattern, irc_lower(hostmask)) is not None


def strip_formatting(text: str) -> str:
    """Rimuove i codici colore/grassetto mIRC dal testo."""
    text = re.sub(r"\x03(\d{1,2}(,\d{1,2})?)?", "", text)
    return re.sub(r"[\x02\x0f\x11\x16\x1d\x1e\x1f]", "", text)


def sanitize(text: str) -> str:
    """Impedisce l'iniezione di comandi IRC eliminando CR, LF e NUL."""
    return text.replace("\r", " ").replace("\n", " ").replace("\0", "")


@dataclass
class Prefix:
    nick: str = ""
    user: str = ""
    host: str = ""

    @classmethod
    def parse(cls, raw: str) -> "Prefix":
        nick, _, rest = raw.partition("!")
        user, _, host = rest.partition("@")
        if not rest and "@" in nick:
            nick, _, host = nick.partition("@")
        return cls(nick, user, host)

    @property
    def mask(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"


_TAG_UNESCAPE = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}


@dataclass
class Message:
    command: str
    params: list[str] = field(default_factory=list)
    prefix: Prefix | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, line: str) -> "Message":
        tags: dict[str, str] = {}
        prefix = None
        if line.startswith("@"):
            raw_tags, _, line = line[1:].partition(" ")
            for item in raw_tags.split(";"):
                key, _, value = item.partition("=")
                tags[key] = re.sub(r"\\(.)", lambda m: _TAG_UNESCAPE.get(m.group(1), m.group(1)), value)
            line = line.lstrip(" ")
        if line.startswith(":"):
            raw_prefix, _, line = line[1:].partition(" ")
            prefix = Prefix.parse(raw_prefix)
            line = line.lstrip(" ")
        trailing = None
        if " :" in line:
            line, _, trailing = line.partition(" :")
        elif line.startswith(":"):
            trailing = line[1:]
            line = ""
        parts = line.split()
        command = parts[0].upper() if parts else ""
        params = parts[1:]
        if trailing is not None:
            params.append(trailing)
        return cls(command, params, prefix, tags)

    @property
    def nick(self) -> str:
        return self.prefix.nick if self.prefix else ""

    @property
    def last(self) -> str:
        return self.params[-1] if self.params else ""


def build_line(command: str, *params: str) -> str:
    """Costruisce una riga IRC; l'ultimo parametro diventa ``trailing`` se necessario."""
    params = [sanitize(p) for p in params]
    if params:
        last = params[-1]
        middle = params[:-1]
        if not last or " " in last or last.startswith(":"):
            last = ":" + last
        return " ".join([command, *middle, last])
    return command


def split_message(text: str, max_bytes: int = 400) -> list[str]:
    """Divide un testo lungo in più righe rispettando un limite in byte (UTF-8)."""
    text = sanitize(text)
    chunks: list[str] = []
    while len(text.encode("utf-8")) > max_bytes:
        cut = max_bytes
        while len(text[:cut].encode("utf-8")) > max_bytes:
            cut -= 1
        space = text.rfind(" ", 0, cut)
        if space > max_bytes // 2:
            cut = space
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text or not chunks:
        chunks.append(text)
    return chunks


def parse_isupport_prefix(value: str) -> tuple[str, str]:
    """``(qaohv)~&@%+`` -> (``qaohv``, ``~&@%+``)."""
    match = re.fullmatch(r"\(([^)]*)\)(.*)", value)
    if not match or len(match.group(1)) != len(match.group(2)):
        return "ov", "@+"
    return match.group(1), match.group(2)
