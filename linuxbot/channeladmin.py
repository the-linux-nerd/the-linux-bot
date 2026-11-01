"""Canali creati e chiusi da IRC: ``!crea``, ``!chiudi``, ``!canali``.

I canali creati da IRC stanno nel database (tabella ``managed_channels``) e, a ogni avvio o
``!reload``, si aggiungono a quelli del file di configurazione: per il resto del bot sono canali
come gli altri (ingresso, topic, modi, owner, attività programmate...). Topic, modi, descrizione e
owner vengono dal modello scelto (``channel_templates``).

In IRC un canale nasce quando qualcuno ci entra e sparisce quando resta vuoto: "creare" vuol dire
entrarci e registrarlo su ChanServ, "chiudere" vuol dire togliere la registrazione (DROP) e uscire.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .config import channel_from_template
from .irc import build_line, irc_lower

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.channels")

_DROP_CONFIRM = re.compile(r"\bDROP\s+(?P<chan>[#&]\S+)\s+(?P<token>[^\s.]+)", re.IGNORECASE)
_REGISTERED = re.compile(r"(?i)(is now registered|registered to|has been registered|now registered|registrat)")


class ChannelAdminMixin:
    _pending_register: set[str]
    _pending_drop: dict[str, float]
    _close_requests: dict[tuple[str, str], float]

    # ------------------------------------------------------------------ canali dal database

    def merge_managed_channels(self: "Bot") -> None:
        """Aggiunge alla configurazione i canali creati da IRC (dal database)."""
        if self.memory is None:
            return
        present = {irc_lower(c.name) for c in self.config.channels}
        for row in self.memory.channel_list():
            if irc_lower(row["name"]) in present:
                continue  # nel frattempo è stato messo nel file: vince il file
            template = self.config.channel_templates.get(row["template"], None)
            if row["template"] and template is None:
                log.warning("Il canale %s usa il modello '%s', che non esiste più: lo gestisco senza modello",
                            row["name"], row["template"])
            cfg = channel_from_template(row["name"], template or {}, row["key"])
            cfg.managed, cfg.template = True, row["template"]
            self.config.channels.append(cfg)

    def _is_static(self: "Bot", channel: str) -> bool:
        cfg = self._channel_cfg(channel)
        return cfg is not None and not cfg.managed

    # ------------------------------------------------------------------ !crea

    def _action_channel_create(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        mgmt = self.config.channel_management
        name = self._r(opts.get("channel", "{arg1}"), ctx).strip()
        template = (self._r(opts.get("template", "{arg2}"), ctx).strip() or mgmt.default_template).lower()
        key = self._r(opts.get("key", ""), ctx).strip()
        reply = lambda text: self.notice(ctx.get("nick") or ctx["_target"], text)  # noqa: E731
        if self.memory is None:
            reply("Per creare canali serve il database (memory.enabled, bans.enabled o karma.enabled).")
            return
        if not re.fullmatch(r"[#&][^\s,\x07]{1,49}", name):
            reply("Indica il canale, es. !crea #progetto [modello]")
            return
        if self._channel_cfg(name) is not None:
            where = "è già nel file di configurazione" if self._is_static(name) else "è già gestito da me"
            reply(f"{name} {where}.")
            return
        if template and template not in self.config.channel_templates:
            names = ", ".join(self.config.channel_templates) or "nessuno"
            reply(f"Modello sconosciuto: {template}. Disponibili: {names}.")
            return
        managed = [c for c in self.config.channels if c.managed]
        if len(managed) >= mgmt.max_channels:
            reply(f"Ho già {len(managed)} canali creati (massimo {mgmt.max_channels}): chiudine qualcuno.")
            return
        cfg = channel_from_template(name, self.config.channel_templates.get(template, {}), key)
        cfg.managed, cfg.template = True, template
        self.memory.channel_add(name, template, key, ctx.get("nick", ""))
        self.config.channels.append(cfg)
        if mgmt.register:
            self._pending_register.add(irc_lower(name))
        self.send(build_line("JOIN", name, key) if key else build_line("JOIN", name))
        log.info("Canale %s creato da %s (modello: %s)", name, ctx.get("nick"), template or "nessuno")
        steps = ["entro"]
        if mgmt.register:
            steps.append("lo registro su ChanServ")
        if cfg.topic is not None or cfg.modes or cfg.description is not None:
            steps.append("imposto topic, modi e descrizione del modello" if template else "imposto topic e modi")
        reply(f"Creo {name}{f' con il modello {template}' if template else ''}: {', '.join(steps)}. "
              "Lo ricorderò anche dopo un riavvio.")

    def _maybe_register(self: "Bot", channel: str) -> None:
        """Registra su ChanServ un canale appena creato, quando il bot ne è op ed è identificato."""
        key = irc_lower(channel)
        if key not in self._pending_register or not self._is_op_in(channel) or not self._logged_in:
            return
        self._pending_register.discard(key)
        cmd = self.config.channel_management.register_command.replace("{channel}", channel)
        log.info("Registro %s su %s", channel, self.config.self_op.service)
        self.send(build_line("PRIVMSG", self.config.self_op.service, cmd))

    # ------------------------------------------------------------------ !chiudi

    def _action_channel_close(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        mgmt = self.config.channel_management
        name = self._r(opts.get("channel", "{arg1}"), ctx).strip()
        confirm = self._r(opts.get("confirm", "{arg2}"), ctx).strip().lower() in ("conferma", "confirm", "si", "sì")
        who = ctx.get("nick", "")
        reply = lambda text: self.notice(who or ctx["_target"], text)  # noqa: E731
        cfg = self._channel_cfg(name) if name else None
        if cfg is None:
            reply(f"Non gestisco {name or 'quel canale'}." if name else "Indica il canale, es. !chiudi #progetto")
            return
        if not cfg.managed:
            reply(f"{cfg.name} è nel file di configurazione: per non usarlo più toglilo da lì e fai !reload.")
            return
        request = (irc_lower(who), irc_lower(cfg.name))
        now = time.monotonic()
        if not confirm or now - self._close_requests.get(request, -1e9) > mgmt.confirm_seconds:
            self._close_requests[request] = now
            what = " e tolgo la registrazione da ChanServ" if mgmt.drop else ""
            reply(f"Sicuro? Esco da {cfg.name}{what}. Per confermare scrivi "
                  f"{ctx.get('prefix', '!')}chiudi {cfg.name} conferma entro {int(mgmt.confirm_seconds)} secondi.")
            return
        self._close_requests.pop(request, None)
        row = next((r for r in self.memory.channel_list() if irc_lower(r["name"]) == irc_lower(cfg.name)), None) \
            if self.memory else None
        if mgmt.drop:
            self._pending_drop[irc_lower(cfg.name)] = now
            cmd = mgmt.drop_command.replace("{channel}", cfg.name)
            self.send(build_line("PRIVMSG", self.config.self_op.service, cmd))
        if irc_lower(cfg.name) in self.channels:
            self.send(build_line("PART", cfg.name, f"Canale chiuso da {who}"))
        if self.memory is not None:
            self.memory.channel_remove(cfg.name)
        self.config.channels = [c for c in self.config.channels if irc_lower(c.name) != irc_lower(cfg.name)]
        self._pending_register.discard(irc_lower(cfg.name))
        log.info("Canale %s chiuso da %s%s", cfg.name, who, " (registrato)" if row and row["registered"] else "")
        reply(f"{cfg.name} chiuso: sono uscito{' e ho chiesto a ChanServ di togliere la registrazione' if mgmt.drop else ''}. "
              "Il canale sparisce quando esce anche l'ultimo utente.")

    # ------------------------------------------------------------------ risposte di ChanServ

    def _chanadmin_notice(self: "Bot", text: str) -> None:
        now = time.monotonic()
        m = _DROP_CONFIRM.search(text)
        if m and now - self._pending_drop.get(irc_lower(m.group("chan")), -1e9) < 60:
            # ChanServ (es. Atheme) chiede di ripetere DROP con un codice di conferma
            channel = m.group("chan")
            self._pending_drop.pop(irc_lower(channel), None)
            log.info("Confermo a ChanServ la cancellazione di %s", channel)
            self.send(build_line("PRIVMSG", self.config.self_op.service, f"DROP {channel} {m.group('token')}"))
            return
        if _REGISTERED.search(text) and self.memory is not None:
            for cfg in self.config.channels:
                if cfg.managed and irc_lower(cfg.name) in irc_lower(text):
                    self.memory.channel_set_registered(cfg.name)
                    log.info("%s registrato su ChanServ", cfg.name)

    # ------------------------------------------------------------------ !canali

    def channel_status_lines(self: "Bot") -> list[str]:
        rows = {irc_lower(r["name"]): r for r in self.memory.channel_list()} if self.memory else {}
        lines = []
        for cfg in self.config.channels:
            key = irc_lower(cfg.name)
            chan = self.channels.get(key)
            if cfg.managed:
                row = rows.get(key, {})
                when = datetime.fromtimestamp(row["ts"]).strftime("%d/%m/%Y") if row.get("ts") else "?"
                origin = f"creato da {row.get('created_by') or '?'} il {when}"
                if cfg.template:
                    origin += f", modello {cfg.template}"
                origin += ", registrato" if row.get("registered") else ""
            else:
                origin = "dal file"
            if chan is None:
                state = "non ci sono"
            else:
                parts = [f"{len(chan.users)} utenti", "op ✓" if self._is_op_in(cfg.name) else "op ✗"]
                wanted = self.wanted_topic(cfg.name)
                if wanted is not None and chan.topic_known:
                    parts.append("topic ✓" if " ".join(chan.topic.split()) == wanted else "topic ✗")
                if cfg.modes and chan.modes_known:
                    parts.append("modi ✗" if self._mode_changes(cfg.name) else "modi ✓")
                state = ", ".join(parts)
            lines.append(f"{cfg.name} ({origin}): {state}")
        return lines or ["Nessun canale configurato."]

    def _action_channel_list(self: "Bot", value: Any, ctx: dict) -> None:
        target = ctx.get("nick") or ctx["_target"]
        for line in self.channel_status_lines():
            self.notice(target, line)
