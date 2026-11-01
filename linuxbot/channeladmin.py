"""Canali creati e chiusi da IRC: ``!crea``, ``!chiudi``, ``!canali``.

I canali creati da IRC stanno nel database (tabella ``managed_channels``) e, a ogni avvio o
``!reload``, si aggiungono a quelli del file di configurazione: per il resto del bot sono canali
come gli altri (ingresso, topic, modi, owner, attività programmate...). Topic, modi, descrizione e
owner vengono dal modello scelto (``channel_templates``).

In IRC un canale nasce quando qualcuno ci entra e sparisce quando resta vuoto: "creare" vuol dire
entrarci e registrarlo su ChanServ, "chiudere" vuol dire togliere la registrazione (DROP) e uscire.

Un canale può essere permanente (esigenze della community) o temporaneo (un evento): i temporanei
hanno una scadenza, ricevono un avviso prima e si chiudono da soli; ``!proroga`` la sposta o li
rende permanenti.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import ChannelConfig, channel_from_template
from .irc import build_line, irc_lower
from .memory import format_duration, parse_duration
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.channels")

_DROP_CONFIRM = re.compile(r"\bDROP\s+(?P<chan>[#&]\S+)\s+(?P<token>[^\s.]+)", re.IGNORECASE)
_PERMANENT = {"perm", "permanente", "per sempre", "sempre", "forever", "0"}
_FILLER = re.compile(r"^(per|fino|fino al|fino alle|al|alle|il|a|entro)\s+", re.IGNORECASE)


def parse_when(text: str, now: datetime) -> tuple[float | None, str]:
    """Quando deve chiudere un canale: durata ("3d", "6h"), data ("15/11", "15/11 18:00",
    "2026-11-15 18:00") o ora ("18:30", oggi o domani). Restituisce (timestamp, errore);
    timestamp 0 = permanente, None = non valido (con il motivo)."""
    t = " ".join(text.lower().split())
    while _FILLER.match(t):
        t = _FILLER.sub("", t, count=1)
    if not t:
        return None, "manca la durata o la data"
    if t in _PERMANENT:
        return 0.0, ""
    seconds = parse_duration(t)
    if seconds is not None:
        return (0.0, "") if seconds == 0 else (now.timestamp() + seconds, "")
    tz = now.tzinfo
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?:\s+(\d{1,2})[:.](\d{2}))?", t)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2})[:.](\d{2}))?", t)
    only_time = re.fullmatch(r"(\d{1,2})[:.](\d{2})", t)
    try:
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else now.year
            if year < 100:
                year += 2000
            hour, minute = (int(m.group(4)), int(m.group(5))) if m.group(4) else (23, 59)
            when = datetime(year, month, day, hour, minute, tzinfo=tz)
            if not m.group(3) and when <= now:
                when = when.replace(year=year + 1)  # "15/01" a dicembre: l'anno prossimo
        elif iso:
            hour, minute = (int(iso.group(4)), int(iso.group(5))) if iso.group(4) else (23, 59)
            when = datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), hour, minute, tzinfo=tz)
        elif only_time:
            when = now.replace(hour=int(only_time.group(1)), minute=int(only_time.group(2)), second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=1)
        else:
            return None, f"non capisco \"{text.strip()}\" (es. 3d, 6h, 15/11, 15/11 18:00, perm)"
    except ValueError:
        return None, f"data non valida: {text.strip()}"
    if when <= now:
        return None, f"{when.strftime('%d/%m/%Y %H:%M')} è già passato"
    return when.timestamp(), ""


_REGISTERED = re.compile(r"(?i)(is now registered|registered to|has been registered|now registered|registrat)")


class ChannelAdminMixin:
    EXPIRY_CHECK = 30.0  # secondi fra un controllo delle scadenze e l'altro
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
            cfg.managed, cfg.template, cfg.expires = True, row["template"], row["expires"]
            self.config.channels.append(cfg)

    def expiry_texts(self: "Bot", cfg: ChannelConfig) -> dict[str, str]:
        """Variabili {expires} ("15/11/2026 18:00") e {expires_in} ("fra 2 giorni") di un canale."""
        if not cfg.expires:
            return {"expires": "", "expires_in": ""}
        tz = self.now_local().tzinfo
        when = datetime.fromtimestamp(cfg.expires, tz).strftime("%d/%m/%Y %H:%M")
        remaining = cfg.expires - self.wallclock()
        return {"expires": when, "expires_in": f"fra {format_duration(remaining)}" if remaining > 60 else "a momenti"}

    def _is_static(self: "Bot", channel: str) -> bool:
        cfg = self._channel_cfg(channel)
        return cfg is not None and not cfg.managed

    # ------------------------------------------------------------------ !crea

    def _action_channel_create(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        mgmt = self.config.channel_management
        name = self._r(opts.get("channel", "{arg1}"), ctx).strip()
        key = self._r(opts.get("key", ""), ctx).strip()
        reply = lambda text: self.notice(ctx.get("nick") or ctx["_target"], text)  # noqa: E731
        temporary = bool(opts.get("temporary"))  # !evento: la scadenza è obbligatoria
        # "spec": il resto della riga, es. "evento 3d" o "15/11 18:00 privato": modello e scadenza
        spec_words = self._r(opts.get("spec", ""), ctx).split()
        template = self._r(opts.get("template", ""), ctx).strip().lower()
        if not template:
            template = next((w.lower() for w in spec_words if w.lower() in self.config.channel_templates), "")
            spec_words = [w for w in spec_words if w.lower() != template] if template else spec_words
        when_text = " ".join(spec_words) or self._r(opts.get("duration", ""), ctx).strip()
        if not template:
            template = (mgmt.event_template if temporary and mgmt.event_template else mgmt.default_template)
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
        tpl = self.config.channel_templates.get(template, {})
        expires: float | None = None
        if when_text:
            ts, error = parse_when(when_text, self.now_local())
            if ts is None:
                reply(f"Scadenza non valida: {error}.")
                return
            expires = ts or None
        elif tpl.get("duration") is not None:
            ts, _ = parse_when(str(tpl["duration"]), self.now_local())
            expires = ts or None
        if temporary and expires is None:
            reply("Per un canale temporaneo indica quando chiude, es. !evento #hackathon 3d o "
                  "!evento #hackathon 15/11 18:00")
            return
        cfg = channel_from_template(name, tpl, key)
        cfg.managed, cfg.template, cfg.expires = True, template, expires
        self.memory.channel_add(name, template, key, ctx.get("nick", ""), expires)
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
        if expires:
            texts = self.expiry_texts(cfg)
            end = f"È temporaneo: chiude {texts['expires_in']} ({texts['expires']}); per spostarlo !proroga."
        else:
            end = "È permanente: lo ricorderò anche dopo un riavvio."
        reply(f"Creo {name}{f' con il modello {template}' if template else ''}: {', '.join(steps)}. {end}")

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
        self.close_managed_channel(cfg, f"Canale chiuso da {who}", by=who)
        reply(f"{cfg.name} chiuso: sono uscito{' e ho chiesto a ChanServ di togliere la registrazione' if mgmt.drop else ''}. "
              "Il canale sparisce quando esce anche l'ultimo utente.")

    def close_managed_channel(self: "Bot", cfg: ChannelConfig, part_reason: str, by: str = "",
                              farewell: bool = False) -> None:
        """Chiude un canale creato da IRC: saluto (facoltativo), DROP su ChanServ, uscita, oblio."""
        mgmt = self.config.channel_management
        present = irc_lower(cfg.name) in self.channels
        if farewell and present and mgmt.close_message:
            self.say(cfg.name, render(mgmt.close_message, {**self._base_context(None, cfg.name)}))
        if mgmt.drop:
            self._pending_drop[irc_lower(cfg.name)] = time.monotonic()
            cmd = mgmt.drop_command.replace("{channel}", cfg.name)
            self.send(build_line("PRIVMSG", self.config.self_op.service, cmd))
        if present:
            self.send(build_line("PART", cfg.name, part_reason))
        if self.memory is not None:
            self.memory.channel_remove(cfg.name)
        self.config.channels = [c for c in self.config.channels if irc_lower(c.name) != irc_lower(cfg.name)]
        self._pending_register.discard(irc_lower(cfg.name))
        log.info("Canale %s chiuso%s", cfg.name, f" da {by}" if by else "")

    # ------------------------------------------------------------------ canali temporanei

    def check_channel_expiry(self: "Bot") -> list[str]:
        """Avvisa i canali temporanei vicini alla scadenza e chiude quelli scaduti. Restituisce i chiusi."""
        if not self.registered or self.memory is None:
            return []
        mgmt = self.config.channel_management
        warn = parse_duration(mgmt.warn_before) if mgmt.warn_before else 0
        rows = {irc_lower(r["name"]): r for r in self.memory.channel_list()}
        now = self.wallclock()
        closed = []
        for cfg in [c for c in self.config.channels if c.managed and c.expires]:
            remaining = cfg.expires - now
            row = rows.get(irc_lower(cfg.name), {})
            if remaining <= 0:
                log.info("Il canale temporaneo %s è scaduto: lo chiudo", cfg.name)
                self.close_managed_channel(cfg, "Canale temporaneo chiuso: evento terminato", farewell=True)
                closed.append(cfg.name)
            elif warn and remaining <= warn and not row.get("warned") and irc_lower(cfg.name) in self.channels:
                self.say(cfg.name, render(mgmt.warn_message, self._base_context(None, cfg.name)))
                self.memory.channel_set_warned(cfg.name)
        return closed

    async def _channel_expiry_loop(self: "Bot") -> None:
        while True:
            try:
                self.check_channel_expiry()
            except Exception:
                log.exception("Errore controllando i canali temporanei")
            await asyncio.sleep(self.EXPIRY_CHECK)

    def _action_channel_extend(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        name = self._r(opts.get("channel", "{arg1}"), ctx).strip()
        when_text = self._r(opts.get("when", "{rest}"), ctx).strip()
        reply = lambda text: self.notice(ctx.get("nick") or ctx["_target"], text)  # noqa: E731
        cfg = self._channel_cfg(name) if name else None
        if cfg is None or not cfg.managed:
            reply(f"{name or 'Quel canale'} non è un canale creato da me." if name else
                  "Indica il canale, es. !proroga #hackathon 2d")
            return
        ts, error = parse_when(when_text, self.now_local())
        if ts is None:
            reply(f"Scadenza non valida: {error}.")
            return
        cfg.expires = ts or None
        if self.memory is not None:
            self.memory.channel_set_expiry(cfg.name, cfg.expires)
        if cfg.expires:
            texts = self.expiry_texts(cfg)
            reply(f"{cfg.name} ora chiude {texts['expires_in']} ({texts['expires']}).")
            log.info("Scadenza di %s spostata al %s da %s", cfg.name, texts["expires"], ctx.get("nick"))
        else:
            reply(f"{cfg.name} ora è permanente.")
            log.info("%s reso permanente da %s", cfg.name, ctx.get("nick"))
        self.check_channel_settings(cfg.name, "scadenza cambiata")  # il topic può contenere {expires}

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
                if cfg.expires:
                    texts = self.expiry_texts(cfg)
                    origin += f", temporaneo: chiude {texts['expires_in']} ({texts['expires']})"
                else:
                    origin += ", permanente"
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
