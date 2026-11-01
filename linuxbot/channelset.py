"""Topic e descrizione dei canali come da configurazione (``channels[].topic`` / ``description``).

- Topic: quando il bot conosce il topic attuale (all'ingresso) e ha l'op, lo confronta con quello
  configurato e lo imposta se è diverso. Con ``topic_lock`` lo rimette se qualcuno lo cambia.
- Modi: chiede i modi attuali al server (``MODE #canale``), li confronta con ``modes`` e manda
  solo le modifiche necessarie; con ``modes_lock`` li rimette se qualcuno li cambia.
- Descrizione: è un dato di ChanServ, non di IRC. Il bot chiede ``INFO #canale`` a ChanServ,
  legge la riga "Description:" e, se è diversa, la aggiorna (``SET DESC`` su Anope). Atheme non
  ha una descrizione del canale: in quel caso lo scrive nel log e non fa niente.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .config import ChannelConfig
from .irc import build_line, irc_lower
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.channels")

DESCRIPTION_COMMANDS = {"anope": "SET DESC {channel} {description}", "other": "SET DESC {channel} {description}"}
_DESC_LINE = re.compile(r"^\s*(description|descrizione)\s*:\s*(?P<desc>.*?)\s*$", re.IGNORECASE)
INFO_WAIT = 6.0


_ABSENT = object()


def _norm(text: str) -> str:
    return " ".join(text.split())


def _hide_key(text: str) -> str:
    """Nei log la chiave del canale (+k) non compare."""
    return re.sub(r"([+-]k) \S+", r"\1 ********", text)


class ChannelSettingsMixin:
    INFO_WAIT = INFO_WAIT
    _desc_waiting: dict[str, dict]   # canale in attesa della risposta a INFO -> {"lines", "found"}
    _desc_queue: list[str]           # canali da controllare (uno alla volta: le risposte non si mescolano)
    _desc_done: set[str]
    _topic_warned: set[str]
    _modes_queried: set[str]
    _modes_reverts: dict[str, list[float]]
    MAX_MODE_REVERTS = 3  # ripristini in un minuto, poi si ferma (es. conflitto con un MLOCK di ChanServ)

    def _channel_cfg(self: "Bot", channel: str) -> ChannelConfig | None:
        return next((c for c in self.config.channels if irc_lower(c.name) == irc_lower(channel)), None)

    def wanted_topic(self: "Bot", channel: str) -> str | None:
        cfg = self._channel_cfg(channel)
        if cfg is None or cfg.topic is None:
            return None
        return _norm(render(cfg.topic, self._base_context(None, channel)))

    # ------------------------------------------------------------------ modi

    def _mode_kind(self: "Bot", mode: str) -> str:
        """Tipo del modo secondo CHANMODES del server: A lista, B sempre con parametro,
        C con parametro solo quando si imposta, D senza parametro."""
        lists, always, on_set, plain = self.chanmodes
        if mode in lists:
            return "A"
        if mode in always:
            return "B"
        if mode in on_set:
            return "C"
        return "D"

    def parse_modes(self: "Bot", spec: str) -> dict[str, tuple[bool, str | None]]:
        """``"+ntl 50 -s"`` -> {"n": (True, None), "t": (True, None), "l": (True, "50"), "s": (False, None)}."""
        wanted: dict[str, tuple[bool, str | None]] = {}
        pending: list[str] = []
        for token in spec.split():
            if re.fullmatch(r"[+-][A-Za-z+-]*", token):
                adding = True
                for ch in token:
                    if ch in "+-":
                        adding = ch == "+"
                        continue
                    kind = self._mode_kind(ch)
                    if kind == "A" or ch in self.prefix_modes:
                        continue
                    wanted[ch] = (adding, None)
                    if adding and kind in "BC":
                        pending.append(ch)
            elif pending:
                ch = pending.pop(0)
                wanted[ch] = (True, token)
        for ch in pending:
            log.warning("Modo +%s senza parametro in channels[].modes: lo ignoro", ch)
            wanted.pop(ch, None)
        return wanted

    def _mode_changes(self: "Bot", channel: str) -> list[tuple[str, str, str | None]]:
        """Modifiche necessarie per arrivare ai modi configurati: [("+", "n", None), ("-", "k", "chiave")...]."""
        cfg = self._channel_cfg(channel)
        chan = self.channels.get(irc_lower(channel))
        if cfg is None or chan is None or not cfg.modes:
            return []
        changes = []
        for mode, (on, param) in self.parse_modes(render(cfg.modes, self._base_context(None, channel))).items():
            current = chan.modes.get(mode, _ABSENT)
            if on and (current is _ABSENT or (param is not None and current != param)):
                changes.append(("+", mode, param))
            elif not on and current is not _ABSENT:
                # per togliere la chiave molti server vogliono anche il parametro
                changes.append(("-", mode, (current or "*") if self._mode_kind(mode) == "B" else None))
        return changes

    def _send_modes(self: "Bot", channel: str, changes: list[tuple[str, str, str | None]]) -> None:
        """Invia le modifiche; il limite dei server (MODES, di solito 3) vale per i modi con parametro."""
        lines: list[list[tuple[str, str, str | None]]] = [[]]
        for change in changes:
            current = lines[-1]
            with_param = sum(1 for c in current if c[2] is not None)
            if len(current) >= 12 or (change[2] is not None and with_param >= 3):
                lines.append([])
            lines[-1].append(change)
        for group in lines:
            letters, args, sign = "", [], ""
            for s_, mode, param in group:
                if s_ != sign:
                    letters += s_
                    sign = s_
                letters += mode
                if param is not None:
                    args.append(param)
            self.send(build_line("MODE", channel, letters, *args) if args else build_line("MODE", channel, letters))

    def _check_modes(self: "Bot", channel: str, reason: str = "") -> bool:
        cfg = self._channel_cfg(channel)
        chan = self.channels.get(irc_lower(channel))
        if cfg is None or chan is None or not cfg.modes:
            return False
        if not chan.modes_known:
            if irc_lower(channel) not in self._modes_queried:
                self._modes_queried.add(irc_lower(channel))
                self.send(build_line("MODE", channel))  # risponde con 324: poi si ricontrolla
            return False
        changes = self._mode_changes(channel)
        if not changes:
            return False
        if not self._is_op_in(channel):
            key = irc_lower(channel) + ":modes"
            if key not in self._topic_warned:
                self._topic_warned.add(key)
                log.warning("I modi di %s sono diversi da quelli configurati, ma non ho l'op per cambiarli", channel)
            return False
        text = " ".join(f"{s_}{m}" + (f" {p}" if p is not None else "") for s_, m, p in changes)
        log.info("Modi di %s da correggere%s: %s", channel, f" ({reason})" if reason else "", _hide_key(text))
        self._send_modes(channel, changes)
        return True

    def _channel_settings_on_modes(self: "Bot", channel: str, setter: str) -> None:
        """Qualcuno ha cambiato i modi: con modes_lock li rimette (con un freno contro i conflitti)."""
        cfg = self._channel_cfg(channel)
        if cfg is None or not cfg.modes_lock or not cfg.modes or self._is_me(setter) or not self._mode_changes(channel):
            return
        now = time.monotonic()
        recent = [t for t in self._modes_reverts.get(irc_lower(channel), []) if now - t < 60]
        if len(recent) >= self.MAX_MODE_REVERTS:
            if len(recent) == self.MAX_MODE_REVERTS:
                log.error("I modi di %s continuano a essere cambiati (da %s): smetto di ripristinarli. "
                          "C'è un MLOCK di ChanServ diverso dal file di configurazione?", channel, setter)
                recent.append(now)
                self._modes_reverts[irc_lower(channel)] = recent
            return
        recent.append(now)
        self._modes_reverts[irc_lower(channel)] = recent
        self._check_modes(channel, f"cambiati da {setter}")

    def _on_channel_modes(self: "Bot", channel: str, modestring: str, args: list[str]) -> None:
        """324 RPL_CHANNELMODEIS: i modi attuali del canale."""
        chan = self.channels.get(irc_lower(channel))
        if chan is None:
            return
        chan.modes = {}
        args = list(args)
        for ch in modestring.lstrip("+"):
            kind = self._mode_kind(ch)
            chan.modes[ch] = args.pop(0) if kind in "BC" and args else None
        chan.modes_known = True
        self._check_modes(channel, "ingresso")

    # ------------------------------------------------------------------ controllo

    def check_channel_settings(self: "Bot", channel: str, reason: str = "") -> list[str]:
        """Allinea topic e descrizione di ``channel`` alla configurazione. Restituisce cosa ha fatto."""
        done = []
        chan = self.channels.get(irc_lower(channel))
        cfg = self._channel_cfg(channel)
        if chan is None or cfg is None:
            return done
        wanted = self.wanted_topic(channel)
        if wanted is not None and chan.topic_known and _norm(chan.topic) != wanted:
            if self._is_op_in(channel):
                log.info("Topic di %s diverso da quello configurato%s: lo imposto", channel,
                         f" ({reason})" if reason else "")
                self.send(build_line("TOPIC", channel, wanted))
                done.append("topic")
            elif irc_lower(channel) not in self._topic_warned:
                self._topic_warned.add(irc_lower(channel))
                log.warning("Il topic di %s è diverso da quello configurato, ma non ho l'op per cambiarlo", channel)
        if self._check_modes(channel, reason):
            done.append("modes")
        key = irc_lower(channel)
        if (cfg.description is not None and key not in self._desc_done and key not in self._desc_waiting
                and key not in self._desc_queue and self.registered):
            self._desc_queue.append(key)
            done.append("description-check")
            if not self._desc_waiting:
                self._desc_next()
        return done

    def _desc_next(self: "Bot") -> None:
        while self._desc_queue:
            key = self._desc_queue.pop(0)
            chan = self.channels.get(key)
            if chan is None:
                continue
            self._desc_waiting[key] = {"lines": [], "found": None}
            self.send(build_line("PRIVMSG", self.config.self_op.service, f"INFO {chan.name}"))
            self._spawn(self._finish_description(chan.name))
            return

    def check_all_channels(self: "Bot", reason: str = "") -> None:
        for chan in list(self.channels.values()):
            self.check_channel_settings(chan.name, reason)

    # ------------------------------------------------------------------ descrizione via ChanServ

    def _chanserv_notice(self: "Bot", text: str) -> None:
        """Raccoglie le righe di INFO per i canali in attesa."""
        for key, waiting in self._desc_waiting.items():
            waiting["lines"].append(text)
            m = _DESC_LINE.match(text)
            if m and waiting["found"] is None:
                waiting["found"] = m.group("desc")

    async def _finish_description(self: "Bot", channel: str) -> None:
        try:
            await self._apply_description(channel)
        finally:
            self._desc_next()

    async def _apply_description(self: "Bot", channel: str) -> None:
        key = irc_lower(channel)
        await asyncio.sleep(self.INFO_WAIT)  # la risposta di INFO arriva in più righe
        waiting = self._desc_waiting.pop(key, None)
        cfg = self._channel_cfg(channel)
        if waiting is None or cfg is None or cfg.description is None:
            return
        self._desc_done.add(key)
        wanted = _norm(render(cfg.description, self._base_context(None, channel)))
        current = waiting["found"]
        if current is not None and _norm(current) == wanted:
            log.info("Descrizione di %s già corretta", channel)
            return
        command = cfg.description_command
        if not command:
            kind = self.services_kind or "other"
            if kind == "atheme":
                log.warning("I services sono Atheme, che non gestisce la descrizione dei canali: "
                            "ignoro channels[%s].description (usa description_command per un comando "
                            "alternativo, es. \"SET {channel} URL ...\")", channel)
                return
            command = DESCRIPTION_COMMANDS.get(kind, DESCRIPTION_COMMANDS["other"])
        if current is None and not any(waiting["lines"]):
            log.warning("ChanServ non ha risposto a INFO %s: provo comunque a impostare la descrizione", channel)
        log.info("Descrizione di %s: \"%s\" -> \"%s\"", channel, current if current is not None else "?", wanted)
        self.send(build_line("PRIVMSG", self.config.self_op.service,
                             render(command, {"channel": channel, "description": wanted})))

    # ------------------------------------------------------------------ agganci

    def _channel_settings_on_topic(self: "Bot", channel: str, setter: str) -> None:
        cfg = self._channel_cfg(channel)
        if cfg is None or self._is_me(setter):
            return
        wanted = self.wanted_topic(channel)
        if cfg.topic_lock and wanted is not None:
            chan = self.channels.get(irc_lower(channel))
            if chan is not None and _norm(chan.topic) != wanted:
                log.info("%s ha cambiato il topic di %s: rimetto quello configurato (topic_lock)", setter, channel)
                self.check_channel_settings(channel, f"cambiato da {setter}")

    def _action_channel_check(self: "Bot", value: Any, ctx: dict) -> None:
        """Forza il controllo (anche della descrizione) nel canale indicato o in tutti."""
        target = self._r(value, ctx).strip() if value else ""
        channels = [target] if target.startswith(("#", "&")) else [c.name for c in self.channels.values()]
        actions = []
        for channel in channels:
            self._desc_done.discard(irc_lower(channel))
            self._topic_warned.discard(irc_lower(channel))
            self._topic_warned.discard(irc_lower(channel) + ":modes")
            self._modes_reverts.pop(irc_lower(channel), None)
            if self.check_channel_settings(channel, f"richiesto da {ctx.get('nick', '?')}"):
                actions.append(channel)
        who = ctx.get("nick") or ctx["_target"]
        if who and not self._is_me(who):
            self.notice(who, f"Controllo di topic, modi e descrizione avviato in {', '.join(actions)}." if actions
                        else "Topic e modi già corretti, niente da controllare (o non ho l'op).")
