"""Rapporti con i services (NickServ): identificazione, riconoscimento di Anope/Atheme, recupero del nick.

Il bot non ha bisogno di sapere quali services usa il server:
- se il server offre SASL, si identifica lì (con la password di NickServ) e il nick non conta;
- altrimenti manda ``IDENTIFY <account> <password>``, accettato da Anope e Atheme da qualunque nick;
- chiede la versione a NickServ (CTCP VERSION) per sapere se è Anope o Atheme;
- se ha dovuto usare un nick alternativo, se lo riprende con il comando giusto (REGAIN per Atheme,
  RECOVER per Anope) o, se non li riconosce, provando REGAIN, RECOVER e GHOST finché uno funziona.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from .irc import build_line, irc_lower
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.auth")

REGAIN_COMMANDS = {
    "atheme": ["REGAIN {nick} {password}", "GHOST {nick} {password}"],
    "anope": ["RECOVER {nick} {password}", "GHOST {nick} {password}"],
    "other": ["REGAIN {nick} {password}", "RECOVER {nick} {password}", "GHOST {nick} {password}"],
}
UNKNOWN_COMMAND = re.compile(r"(?i)(unknown command|invalid command|is not a valid command|no such command|"
                             r"comando (sconosciuto|non valido|inesistente))")


class ServicesMixin:
    REGAIN_WAIT = 4.0  # secondi di attesa della risposta di NickServ a ogni tentativo
    services_kind: str           # anope | atheme | other | "" (non ancora noto)
    services_version: str
    _logged_in: bool
    _sasl_ok: bool
    _regain_task: "asyncio.Task | None"
    _services_known: "asyncio.Event | None"
    _nickserv_notices: list[str]
    _last_nick_try: float

    # ------------------------------------------------------------------ identificazione

    def _identify_command(self: "Bot") -> str:
        ns = self.config.auth.nickserv
        if ns.command != "auto":
            template = ns.command
        elif irc_lower(self.nick) == irc_lower(ns.account):
            template = "IDENTIFY {password}"
        else:
            template = "IDENTIFY {account} {password}"  # da un nick alternativo: indica l'account
        return render(template, {"password": ns.password, "account": ns.account, "nick": self.nick,
                                 "botnick": self.nick})

    def _services_start(self: "Bot") -> None:
        """A registrazione avvenuta: identificazione (se SASL non l'ha già fatta) e richiesta della versione."""
        ns = self.config.auth.nickserv
        self._services_known = asyncio.Event()
        if ns.services != "auto":
            self._set_services(ns.services, "")
        elif ns.enabled or self.config.auth.sasl.enabled:
            self.send(build_line("PRIVMSG", ns.service, "\x01VERSION\x01"))
        else:
            self._services_known.set()
        if ns.enabled and not self._sasl_ok:
            self.send(build_line("PRIVMSG", ns.service, self._identify_command()), priority=True)
        if self._sasl_ok:
            self._on_logged_in()

    def _set_services(self: "Bot", kind: str, version: str) -> None:
        self.services_kind, self.services_version = kind, version
        if version:
            log.info("Services: %s (%s)", kind, version)
        if self._services_known is not None:
            self._services_known.set()

    def _services_notice(self: "Bot", text: str) -> bool:
        """Gestisce una NOTICE di NickServ. True se era la risposta alla richiesta di versione."""
        if text.startswith("\x01VERSION"):
            version = text.strip("\x01")[len("VERSION"):].strip()
            low = version.lower()
            kind = "anope" if "anope" in low else "atheme" if "atheme" in low else "other"
            if self.config.auth.nickserv.services == "auto":
                self._set_services(kind, version)
            return True
        self._nickserv_notices.append(text)
        return False

    def _on_logged_in(self: "Bot") -> None:
        """Chiamato una volta per connessione quando i services confermano l'identificazione."""
        if self._logged_in:
            return
        self._logged_in = True
        log.info("Identificato con i services%s", " (SASL)" if self._sasl_ok else "")
        self.run_event("identified", self._base_context())
        if self._wants_main_nick() and self.config.auth.nickserv.regain and self._regain_task is None:
            self._regain_task = self._spawn(self._regain_nick())

    # ------------------------------------------------------------------ recupero del nick

    def _main_nick(self: "Bot") -> str:
        return self.config.identity.nick

    def _wants_main_nick(self: "Bot") -> bool:
        return irc_lower(self.nick) != irc_lower(self._main_nick())

    async def _wait_for(self: "Bot", condition, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            await asyncio.sleep(0.1)
        return condition()

    async def _regain_nick(self: "Bot") -> None:
        ns = self.config.auth.nickserv
        main = self._main_nick()
        if self._services_known is not None and not self._services_known.is_set():
            try:
                await asyncio.wait_for(self._services_known.wait(), timeout=self.REGAIN_WAIT)
            except asyncio.TimeoutError:
                pass
        kind = self.services_kind or "other"
        commands = ns.regain_commands or REGAIN_COMMANDS.get(kind, REGAIN_COMMANDS["other"])
        log.info("Il nick %s è occupato: provo a riprenderlo (services: %s)", main, kind)
        for template in commands:
            if not self._wants_main_nick():
                return
            self._nickserv_notices.clear()
            cmd = render(template, {"nick": main, "password": ns.password, "account": ns.account})
            self.send(build_line("PRIVMSG", ns.service, cmd))
            await self._wait_for(lambda: not self._wants_main_nick() or self._nickserv_notices, self.REGAIN_WAIT)
            if not self._wants_main_nick():
                break  # i services ci hanno già cambiato il nick (es. RECOVER di Anope)
            if any(UNKNOWN_COMMAND.search(n) for n in self._nickserv_notices):
                log.debug("NickServ non conosce '%s', provo il successivo", template.split()[0])
                continue
            await asyncio.sleep(0.5)
            self.send(build_line("NICK", main))
            if await self._wait_for(lambda: not self._wants_main_nick(), self.REGAIN_WAIT):
                break
        if self._wants_main_nick():
            log.warning("Non sono riuscito a riprendermi il nick %s: resto %s (lo riprendo appena si libera)",
                        main, self.nick)
        else:
            log.info("Nick %s ripreso", main)

    def _nick_maybe_free(self: "Bot", nick: str) -> None:
        """Chi usava il nick principale è uscito o l'ha cambiato: lo riprende subito."""
        if (self.registered and irc_lower(nick) == irc_lower(self._main_nick()) and self._wants_main_nick()
                and time.monotonic() - self._last_nick_try > 10):
            self._last_nick_try = time.monotonic()
            log.info("Il nick %s si è liberato: lo riprendo", self._main_nick())
            self.send(build_line("NICK", self._main_nick()))
