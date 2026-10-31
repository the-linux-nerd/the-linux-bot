"""Logica del bot: connessione, autenticazione, stato dei canali, comandi, risposte e saluti."""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from . import __version__
from .config import Config, CommandConfig, ConfigError, ResponseConfig, load_config
from .irc import (
    Message, build_line, irc_lower, is_channel, mask_match, parse_isupport_prefix,
    sanitize, split_message, strip_formatting,
)
from .templates import args_variables, pick, render

log = logging.getLogger("linuxbot")

PING_INTERVAL = 180.0


@dataclass
class Channel:
    name: str
    users: dict[str, set[str]] = field(default_factory=dict)  # nick (lower) -> modi (es. {"o", "v"})
    topic: str = ""


class Bot:
    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.nick = config.identity.nick
        self.channels: dict[str, Channel] = {}
        self.hosts: dict[str, str] = {}  # nick (lower) -> nick!user@host
        self.prefix_modes, self.prefix_symbols = "ov", "@+"
        self.chanmodes = ("beI", "k", "l", "")
        self.cooldowns: dict[tuple, float] = {}
        self.registered = False
        self.is_oper = False
        self.server_name = config.server.host
        self._nick_attempt = 0
        self._sasl_in_progress = False
        self._tasks: set[asyncio.Task] = set()
        self._queue: asyncio.Queue[str] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._quitting = False

    # ------------------------------------------------------------------ connessione

    async def run(self) -> None:
        """Connette il bot e lo tiene in vita, riconnettendo con backoff esponenziale."""
        delay = self.config.behavior.reconnect_delay
        while not self._quitting:
            try:
                await self._session()
                delay = self.config.behavior.reconnect_delay
            except (OSError, asyncio.IncompleteReadError, ssl.SSLError, ConnectionError) as exc:
                log.error("Connessione persa: %s", exc)
            if self._quitting:
                break
            log.info("Riconnessione fra %.0f secondi", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.config.behavior.reconnect_max_delay)

    async def _session(self) -> None:
        srv = self.config.server
        context = None
        if srv.tls:
            context = ssl.create_default_context()
            if not srv.tls_verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        log.info("Connessione a %s:%d (TLS: %s)", srv.host, srv.port, "sì" if srv.tls else "no")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(srv.host, srv.port, ssl=context), timeout=30
        )
        self._reset_state()
        self._writer = writer
        self._queue = asyncio.Queue()
        sender = asyncio.create_task(self._sender(writer))
        try:
            self._register()
            await self._read_loop(reader)
        finally:
            sender.cancel()
            for task in list(self._tasks):
                task.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            self._writer = None
            self._queue = None

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        pinged = False
        while True:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                if pinged:
                    raise ConnectionError("il server non risponde") from None
                self.send(build_line("PING", "keepalive"), priority=True)
                pinged = True
                continue
            if not raw:
                raise ConnectionError("connessione chiusa dal server")
            pinged = False
            line = self._decode(raw).rstrip("\r\n")
            if line:
                try:
                    self.handle_line(line)
                except Exception:  # un bug in un handler non deve far cadere il bot
                    log.exception("Errore gestendo la riga: %s", line)

    def _decode(self, raw: bytes) -> str:
        try:
            return raw.decode(self.config.server.encoding)
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    async def _sender(self, writer: asyncio.StreamWriter) -> None:
        """Invia le righe in coda con un token bucket per evitare il flood kick."""
        beh = self.config.behavior
        tokens = float(beh.flood_burst)
        last = self.clock()
        assert self._queue is not None
        while True:
            line = await self._queue.get()
            now = self.clock()
            tokens = min(float(beh.flood_burst), tokens + (now - last) / max(beh.flood_rate, 0.01))
            last = now
            if tokens < 1:
                await asyncio.sleep((1 - tokens) * beh.flood_rate)
                tokens = 1
                last = self.clock()
            tokens -= 1
            self._write(writer, line)
            await writer.drain()

    def _write(self, writer: asyncio.StreamWriter, line: str) -> None:
        log.debug(">> %s", _redact(line))
        writer.write((line + "\r\n").encode(self.config.server.encoding, errors="replace"))

    def send(self, line: str, priority: bool = False) -> None:
        """Accoda una riga da inviare. Le righe prioritarie (PONG, registrazione) saltano la coda."""
        if priority and self._writer is not None:
            self._write(self._writer, line)
        elif self._queue is not None:
            self._queue.put_nowait(line)
        else:
            log.warning("Non connesso, scarto: %s", _redact(line))

    def _reset_state(self) -> None:
        self.nick = self.config.identity.nick
        self.channels.clear()
        self.hosts.clear()
        self.registered = False
        self.is_oper = False
        self._nick_attempt = 0
        self._sasl_in_progress = False

    # ------------------------------------------------------------------ registrazione e auth

    def _register(self) -> None:
        cfg = self.config
        if cfg.auth.sasl.enabled:
            self._sasl_in_progress = True
            self.send("CAP LS 302", priority=True)
        if cfg.server.password:
            self.send(build_line("PASS", cfg.server.password), priority=True)
        self.send(build_line("NICK", self.nick), priority=True)
        self.send(build_line("USER", cfg.identity.username, "0", "*", cfg.identity.realname), priority=True)

    def _next_nick(self) -> str:
        alts = self.config.identity.alt_nicks
        self._nick_attempt += 1
        if self._nick_attempt <= len(alts):
            return alts[self._nick_attempt - 1]
        return f"{self.config.identity.nick}{self._nick_attempt - len(alts)}"

    def _on_cap(self, msg: Message) -> None:
        sub = msg.params[1].upper() if len(msg.params) > 1 else ""
        caps = msg.last.split()
        if sub == "LS":
            if len(msg.params) > 3 and msg.params[2] == "*":
                return  # LS multi-riga, aspettiamo l'ultima
            names = {c.split("=")[0] for c in caps}
            if "sasl" in names and self._sasl_in_progress:
                self.send("CAP REQ :sasl", priority=True)
            else:
                log.error("Il server non supporta SASL")
                self._end_cap()
        elif sub == "ACK" and "sasl" in caps:
            self.send("AUTHENTICATE PLAIN", priority=True)
        elif sub == "NAK":
            log.error("Il server ha rifiutato la capability SASL")
            self._end_cap()

    def _on_authenticate(self, msg: Message) -> None:
        if msg.params and msg.params[0] == "+":
            sasl = self.config.auth.sasl
            payload = f"{sasl.username}\0{sasl.username}\0{sasl.password}".encode()
            encoded = base64.b64encode(payload).decode()
            for i in range(0, len(encoded), 400):
                self.send(f"AUTHENTICATE {encoded[i:i + 400]}", priority=True)
            if len(encoded) % 400 == 0:
                self.send("AUTHENTICATE +", priority=True)

    def _end_cap(self) -> None:
        if self._sasl_in_progress:
            self._sasl_in_progress = False
            self.send("CAP END", priority=True)

    def _on_registered(self) -> None:
        """Chiamato a fine MOTD: OPER, NickServ, modi utente, evento connect, join."""
        if self.registered:
            return
        self.registered = True
        auth = self.config.auth
        log.info("Registrato come %s su %s", self.nick, self.server_name)
        if auth.oper.enabled:
            self.send(build_line("OPER", auth.oper.name, auth.oper.password), priority=True)
        if auth.user_modes:
            self.send(build_line("MODE", self.nick, auth.user_modes))
        wait = 0.0
        if auth.nickserv.enabled:
            cmd = render(auth.nickserv.command, {"password": auth.nickserv.password, "nick": self.nick,
                                                 "botnick": self.nick})
            self.send(build_line("PRIVMSG", auth.nickserv.service, cmd), priority=True)
            wait = auth.nickserv.wait
        self.run_event("connect", self._base_context())
        self._spawn(self._join_channels(wait))

    async def _join_channels(self, wait: float = 0.0) -> None:
        if wait:
            await asyncio.sleep(wait)
        for ch in self.config.channels:
            if irc_lower(ch.name) not in self.channels:
                self.send(build_line("JOIN", ch.name, ch.key) if ch.key else build_line("JOIN", ch.name))

    # ------------------------------------------------------------------ dispatch

    def handle_line(self, line: str) -> None:
        log.debug("<< %s", _redact(line))
        msg = Message.parse(line)
        handler = getattr(self, f"_on_{msg.command.lower()}", None)
        if msg.command.isdigit():
            self._on_numeric(msg)
        elif handler:
            handler(msg)

    def _on_ping(self, msg: Message) -> None:
        self.send(build_line("PONG", msg.last), priority=True)

    def _on_error(self, msg: Message) -> None:
        log.error("Errore dal server: %s", msg.last)

    def _on_numeric(self, msg: Message) -> None:
        code = msg.command
        if code == "001":
            self.nick = msg.params[0]
            if msg.prefix:
                self.server_name = msg.prefix.nick or msg.prefix.host
        elif code == "005":
            for token in msg.params[1:-1]:
                key, _, value = token.partition("=")
                if key == "PREFIX" and value:
                    self.prefix_modes, self.prefix_symbols = parse_isupport_prefix(value)
                elif key == "CHANMODES" and value.count(",") >= 3:
                    a, b, c, d = value.split(",")[:4]
                    self.chanmodes = (a, b, c, d)
        elif code in ("376", "422"):  # fine MOTD / MOTD assente
            self._on_registered()
        elif code in ("432", "433", "436", "437") and not self.registered:
            new = self._next_nick()
            log.warning("Nick %s non disponibile, provo %s", self.nick, new)
            self.nick = new
            self.send(build_line("NICK", new), priority=True)
        elif code == "381":
            self.is_oper = True
            log.info("Ora sono IRC operator")
        elif code in ("464", "491"):
            log.error("OPER fallito: %s", msg.last)
        elif code in ("900", "903"):
            if code == "903":
                log.info("Autenticazione SASL riuscita")
                self._end_cap()
        elif code in ("902", "904", "905", "906", "908"):
            log.error("Autenticazione SASL fallita: %s", msg.last)
            self._end_cap()
        elif code == "353" and len(msg.params) >= 4:  # NAMES
            chan = self.channels.get(irc_lower(msg.params[2]))
            if chan:
                for entry in msg.last.split():
                    modes = set()
                    while entry and entry[0] in self.prefix_symbols:
                        modes.add(self.prefix_modes[self.prefix_symbols.index(entry[0])])
                        entry = entry[1:]
                    nick = entry.split("!")[0]
                    if "!" in entry:
                        self.hosts[irc_lower(nick)] = entry
                    chan.users[irc_lower(nick)] = modes
        elif code == "332" and len(msg.params) >= 3:
            chan = self.channels.get(irc_lower(msg.params[1]))
            if chan:
                chan.topic = msg.last
        elif code in ("471", "473", "474", "475"):
            log.error("Impossibile entrare in %s: %s", msg.params[1] if len(msg.params) > 1 else "?", msg.last)

    def _is_me(self, nick: str) -> bool:
        return irc_lower(nick) == irc_lower(self.nick)

    def _remember(self, msg: Message) -> None:
        if msg.prefix and msg.prefix.user:
            self.hosts[irc_lower(msg.nick)] = msg.prefix.mask

    # ------------------------------------------------------------------ eventi di canale

    def _on_join(self, msg: Message) -> None:
        channel = msg.params[0] if msg.params else ""
        key = irc_lower(channel)
        self._remember(msg)
        ctx = self._base_context(msg, channel)
        if self._is_me(msg.nick):
            self.channels[key] = Channel(channel)
            log.info("Entrato in %s", channel)
            self.run_event("self_join", ctx)
            return
        chan = self.channels.get(key)
        if chan is not None:
            chan.users[irc_lower(msg.nick)] = set()
        self.run_event("join", ctx)
        self._greet(msg, channel, ctx)

    def _on_part(self, msg: Message) -> None:
        channel = msg.params[0] if msg.params else ""
        if self._is_me(msg.nick):
            self.channels.pop(irc_lower(channel), None)
            return
        chan = self.channels.get(irc_lower(channel))
        if chan:
            chan.users.pop(irc_lower(msg.nick), None)
        ctx = self._base_context(msg, channel)
        ctx["reason"] = msg.params[1] if len(msg.params) > 1 else ""
        self.run_event("part", ctx)

    def _on_kick(self, msg: Message) -> None:
        channel, victim = msg.params[0], msg.params[1]
        ctx = self._base_context(msg, channel)
        ctx.update(victim=victim, kicker=msg.nick, reason=msg.params[2] if len(msg.params) > 2 else "")
        if self._is_me(victim):
            self.channels.pop(irc_lower(channel), None)
            log.warning("Espulso da %s da %s: %s", channel, msg.nick, ctx["reason"])
            self.run_event("self_kick", ctx)
            if self.config.behavior.rejoin_on_kick:
                self._spawn(self._rejoin(channel))
            return
        chan = self.channels.get(irc_lower(channel))
        if chan:
            chan.users.pop(irc_lower(victim), None)
        self.run_event("kick", ctx)

    async def _rejoin(self, channel: str) -> None:
        await asyncio.sleep(self.config.behavior.rejoin_delay)
        key = next((c.key for c in self.config.channels if irc_lower(c.name) == irc_lower(channel)), None)
        self.send(build_line("JOIN", channel, key) if key else build_line("JOIN", channel))

    def _on_quit(self, msg: Message) -> None:
        nick = irc_lower(msg.nick)
        for chan in self.channels.values():
            chan.users.pop(nick, None)
        self.hosts.pop(nick, None)
        ctx = self._base_context(msg)
        ctx["reason"] = msg.last
        self.run_event("quit", ctx)

    def _on_nick(self, msg: Message) -> None:
        old, new = irc_lower(msg.nick), msg.last
        if self._is_me(msg.nick):
            self.nick = new
        for chan in self.channels.values():
            if old in chan.users:
                chan.users[irc_lower(new)] = chan.users.pop(old)
        if old in self.hosts:
            self.hosts[irc_lower(new)] = self.hosts.pop(old).replace(msg.nick, new, 1)
        ctx = self._base_context(msg)
        ctx.update(old_nick=msg.nick, new_nick=new)
        self.run_event("nick", ctx)

    def _on_topic(self, msg: Message) -> None:
        chan = self.channels.get(irc_lower(msg.params[0]))
        if chan:
            chan.topic = msg.last
        ctx = self._base_context(msg, msg.params[0])
        ctx["topic"] = msg.last
        self.run_event("topic", ctx)

    def _on_invite(self, msg: Message) -> None:
        ctx = self._base_context(msg, msg.last)
        self.run_event("invite", ctx)

    def _on_mode(self, msg: Message) -> None:
        if len(msg.params) < 2 or not is_channel(msg.params[0]):
            return
        chan = self.channels.get(irc_lower(msg.params[0]))
        if not chan:
            return
        args = msg.params[2:]
        adding = True
        for ch in msg.params[1]:
            if ch in "+-":
                adding = ch == "+"
                continue
            takes_arg = (ch in self.prefix_modes or ch in self.chanmodes[0] or ch in self.chanmodes[1]
                         or (adding and ch in self.chanmodes[2]))
            arg = args.pop(0) if takes_arg and args else None
            if ch in self.prefix_modes and arg is not None:
                modes = chan.users.setdefault(irc_lower(arg), set())
                (modes.add if adding else modes.discard)(ch)

    # ------------------------------------------------------------------ messaggi

    def _on_notice(self, msg: Message) -> None:
        if msg.nick and irc_lower(msg.nick) == irc_lower(self.config.auth.nickserv.service):
            log.info("%s: %s", msg.nick, strip_formatting(msg.last))

    def _on_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2 or not msg.prefix or self._is_me(msg.nick):
            return
        self._remember(msg)
        target, text = msg.params[0], msg.last
        if text.startswith("\x01"):
            self._on_ctcp(msg, text.strip("\x01"))
            return
        private = not is_channel(target)
        beh = self.config.behavior
        if private and not beh.respond_to_private:
            return
        mask = msg.prefix.mask
        is_admin = self.is_admin(mask)
        if not is_admin and any(mask_match(m, mask) for m in beh.ignore):
            return
        channel = "" if private else target
        ctx = self._base_context(msg, channel)
        text = strip_formatting(text).strip()
        ctx["message"] = text

        command_text = self._extract_command(text)
        addressed = False
        if command_text is None and beh.answer_when_addressed:
            m = re.match(rf"^{re.escape(self.nick)}[:,]?\s+(.*)$", text, re.IGNORECASE)
            if m:
                addressed = True
                rest = m.group(1).strip()
                command_text = self._extract_command(rest)
                if command_text is None and rest.split() and self.config.find_command(rest.split()[0]):
                    command_text = rest
                if command_text is None:
                    text = rest
        if private:
            addressed = True
        elif re.search(rf"(?<!\w){re.escape(self.nick)}(?!\w)", text, re.IGNORECASE):
            addressed = True

        if command_text is not None:
            self._handle_command(command_text, ctx, is_admin, private)
        else:
            self._handle_responses(text, ctx, is_admin, private, addressed)

    def _extract_command(self, text: str) -> str | None:
        for prefix in self.config.behavior.command_prefixes:
            if prefix and text.startswith(prefix) and len(text) > len(prefix):
                return text[len(prefix):]
        return None

    def _on_ctcp(self, msg: Message, body: str) -> None:
        cmd, _, arg = body.partition(" ")
        cmd = cmd.upper()
        reply = None
        if cmd == "VERSION":
            reply = f"VERSION {self.config.identity.version}"
        elif cmd == "PING":
            reply = f"PING {arg}"
        elif cmd == "TIME":
            reply = f"TIME {datetime.now().strftime('%a %b %d %H:%M:%S %Y')}"
        if reply:
            self.send(build_line("NOTICE", msg.nick, f"\x01{reply}\x01"))

    # ------------------------------------------------------------------ comandi

    def _handle_command(self, command_text: str, ctx: dict, is_admin: bool, private: bool) -> None:
        parts = command_text.split()
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        prefix = self.config.behavior.command_prefixes[0] if self.config.behavior.command_prefixes else ""
        ctx.update(args_variables(args, ctx["nick"]))
        ctx["command"] = f"{prefix}{name}"
        cmd = self.config.find_command(name)
        beh = self.config.behavior
        if cmd is None:
            if beh.unknown_command_reply:
                self.say(ctx["_target"], render(beh.unknown_command_reply, ctx))
            return
        ctx["command"] = f"{prefix}{cmd.name}"
        if private and not cmd.private:
            return
        if not private and cmd.channels and not self._channel_in(ctx["channel"], cmd.channels):
            return
        if not self.has_permission(ctx["mask"], cmd.permission, ctx["channel"], is_admin):
            if beh.permission_denied_reply:
                self.notice(ctx["nick"], render(beh.permission_denied_reply, ctx))
            return
        if len(args) < cmd.min_args:
            usage = f"{prefix}{cmd.name} {cmd.usage}".strip()
            self.notice(ctx["nick"], f"Uso: {usage}")
            return
        key = ("command", cmd.name, irc_lower(ctx["channel"] or ctx["nick"]))
        if not is_admin and not self._cooldown_ok(key, cmd.cooldown):
            if beh.cooldown_reply:
                self.notice(ctx["nick"], render(beh.cooldown_reply, ctx))
            return
        log.info("Comando %s da %s in %s", cmd.name, ctx["nick"], ctx["channel"] or "privato")
        self._run_reply_and_actions(cmd, ctx)

    def _run_reply_and_actions(self, item: CommandConfig | ResponseConfig, ctx: dict) -> None:
        text = pick(item.reply)
        if text is not None:
            rendered = render(text, ctx)
            if item.method == "notice":
                self.notice(ctx["nick"], rendered)
            else:
                self.say(ctx["_target"], rendered)
        if item.actions:
            self.run_actions(item.actions, ctx)

    # ------------------------------------------------------------------ risposte automatiche

    def _handle_responses(self, text: str, ctx: dict, is_admin: bool, private: bool, addressed: bool) -> None:
        for resp in self.config.responses:
            if private and not resp.private:
                continue
            if not private and resp.channels and not self._channel_in(ctx["channel"], resp.channels):
                continue
            if resp.addressed and not addressed:
                continue
            match = next((m for m in (rx.search(text) for rx in resp.compiled) if m), None)
            if not match:
                continue
            if not self.has_permission(ctx["mask"], resp.permission, ctx["channel"], is_admin):
                continue
            if resp.probability < 1.0 and random.random() > resp.probability:
                continue
            key = ("response", resp.name, irc_lower(ctx["channel"] or ctx["nick"]))
            if not self._cooldown_ok(key, resp.cooldown):
                if resp.stop:
                    return
                continue
            rctx = dict(ctx)
            rctx["match"] = match.group(0)
            for i, group in enumerate(match.groups(), start=1):
                rctx[f"group{i}"] = group or ""
            rctx.update({k: v or "" for k, v in match.groupdict().items()})
            log.info("Risposta '%s' per %s in %s", resp.name, ctx["nick"], ctx["channel"] or "privato")
            self._run_reply_and_actions(resp, rctx)
            if resp.stop:
                return

    # ------------------------------------------------------------------ saluti

    def _greet(self, msg: Message, channel: str, ctx: dict) -> None:
        gr = self.config.greetings
        if not gr.enabled or (gr.channels and not self._channel_in(channel, gr.channels)):
            return
        mask = msg.prefix.mask if msg.prefix else msg.nick
        if any(mask_match(m, mask) for m in self.config.behavior.ignore):
            return
        rules = [r for r in gr.rules
                 if any(mask_match(m, mask) for m in r.match)
                 and (not r.channels or self._channel_in(channel, r.channels))]
        if any(r.skip for r in rules):
            return
        key = ("greet", irc_lower(channel), irc_lower(msg.nick))
        if not self._cooldown_ok(key, gr.cooldown):
            return
        messages = next((r.messages for r in rules if r.messages is not None), gr.messages)
        method = next((r.method for r in rules if r.method), gr.method)
        actions = list(gr.actions) + [a for r in rules for a in r.actions]
        self._spawn(self._do_greet(channel, msg.nick, messages, method, actions, ctx))

    async def _do_greet(self, channel: str, nick: str, messages: list[str], method: str,
                        actions: list[dict], ctx: dict) -> None:
        if self.config.greetings.delay:
            await asyncio.sleep(self.config.greetings.delay)
        chan = self.channels.get(irc_lower(channel))
        if chan is not None and irc_lower(nick) not in chan.users:
            return  # è già uscito
        text = pick(messages)
        if text:
            rendered = render(text, ctx)
            if method == "notice":
                self.notice(nick, rendered)
            else:
                self.say(channel, rendered)
        if actions:
            await self.execute_actions(actions, ctx)

    # ------------------------------------------------------------------ permessi e utilità

    def is_admin(self, mask: str) -> bool:
        return any(mask_match(m, mask) for m in self.config.admins)

    def user_modes(self, channel: str, nick: str) -> set[str]:
        chan = self.channels.get(irc_lower(channel))
        return chan.users.get(irc_lower(nick), set()) if chan else set()

    def _rank(self, modes: set[str]) -> int:
        """Indice del modo più alto (0 = il più alto); len(prefix_modes) se nessuno."""
        ranks = [self.prefix_modes.index(m) for m in modes if m in self.prefix_modes]
        return min(ranks) if ranks else len(self.prefix_modes)

    def has_permission(self, mask: str, level: str, channel: str = "", is_admin: bool | None = None) -> bool:
        if is_admin is None:
            is_admin = self.is_admin(mask)
        if level == "anyone" or is_admin:
            return True
        if level == "admin":
            return False
        required = {"voice": "v", "halfop": "h", "op": "o"}[level]
        if required not in self.prefix_modes:
            required = "o"
        need = self.prefix_modes.index(required) if required in self.prefix_modes else 0
        nick = mask.split("!")[0]
        channels = [channel] if channel else [c.name for c in self.channels.values()]
        return any(self._rank(self.user_modes(c, nick)) <= need for c in channels)

    def _cooldown_ok(self, key: tuple, seconds: float) -> bool:
        if seconds <= 0:
            return True
        now = self.clock()
        last = self.cooldowns.get(key)
        if last is not None and now - last < seconds:
            return False
        self.cooldowns[key] = now
        return True

    @staticmethod
    def _channel_in(channel: str, channels: list[str]) -> bool:
        return irc_lower(channel) in {irc_lower(c) for c in channels}

    def _base_context(self, msg: Message | None = None, channel: str = "") -> dict[str, Any]:
        now = datetime.now()
        ctx: dict[str, Any] = dict(self.config.variables)
        nick = msg.nick if msg else ""
        ctx.update(
            botnick=self.nick, server=self.server_name, channel=channel,
            nick=nick, user=msg.prefix.user if msg and msg.prefix else "",
            host=msg.prefix.host if msg and msg.prefix else "",
            mask=msg.prefix.mask if msg and msg.prefix else "",
            date=now.strftime("%d/%m/%Y"), time=now.strftime("%H:%M"),
            weekday=["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"][now.weekday()],
            version=__version__,
            users=str(len(self.channels[irc_lower(channel)].users)) if irc_lower(channel) in self.channels else "0",
            prefix=self.config.behavior.command_prefixes[0] if self.config.behavior.command_prefixes else "",
        )
        ctx["_target"] = channel or nick
        return ctx

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("Errore in un task del bot", exc_info=t.exception())

        task.add_done_callback(done)
        return task

    async def drain(self) -> None:
        """Attende che tutti i task in sospeso (saluti, azioni) siano completati. Utile nei test."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ invio messaggi

    def say(self, target: str, text: str) -> None:
        for line in str(text).splitlines() or [""]:
            if line.strip():
                for chunk in split_message(line, self.config.behavior.max_line_bytes):
                    self.send(build_line("PRIVMSG", target, chunk))

    def notice(self, target: str, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                for chunk in split_message(line, self.config.behavior.max_line_bytes):
                    self.send(build_line("NOTICE", target, chunk))

    # ------------------------------------------------------------------ azioni

    def run_event(self, name: str, ctx: dict) -> None:
        actions = self.config.events.get(name)
        if actions:
            self.run_actions(actions, ctx)

    def run_actions(self, actions: list[dict], ctx: dict) -> None:
        self._spawn(self.execute_actions(actions, ctx))

    async def execute_actions(self, actions: list[dict], ctx: dict) -> None:
        for action in actions:
            name, value = next(iter(action.items()))
            try:
                if name == "delay":
                    await asyncio.sleep(float(value))
                else:
                    getattr(self, f"_action_{name}")(value, ctx)
            except Exception:
                log.exception("Errore eseguendo l'azione %s", name)

    # Ogni azione accetta una stringa (forma breve) oppure una mappa con i dettagli.

    def _r(self, value: Any, ctx: dict) -> str:
        return render(pick(value) or "", ctx)

    def _text_and_target(self, value: Any, ctx: dict, default_target: str) -> tuple[str, str]:
        if isinstance(value, dict):
            return self._r(value.get("text"), ctx), self._r(value.get("to"), ctx) or default_target
        return self._r(value, ctx), default_target

    def _channel_of(self, value: Any, ctx: dict) -> str:
        channel = self._r(value.get("channel"), ctx) if isinstance(value, dict) else ""
        return channel or ctx.get("channel", "")

    def _action_say(self, value: Any, ctx: dict) -> None:
        text, target = self._text_and_target(value, ctx, ctx["_target"])
        if target:
            self.say(target, text)

    _action_reply = _action_say
    _action_msg = _action_say

    def _action_notice(self, value: Any, ctx: dict) -> None:
        text, target = self._text_and_target(value, ctx, ctx.get("nick") or ctx["_target"])
        if target:
            self.notice(target, text)

    def _action_me(self, value: Any, ctx: dict) -> None:
        text, target = self._text_and_target(value, ctx, ctx["_target"])
        if target and text:
            self.send(build_line("PRIVMSG", target, f"\x01ACTION {text}\x01"))

    def _action_kick(self, value: Any, ctx: dict) -> None:
        channel = self._channel_of(value, ctx)
        if isinstance(value, dict):
            nick = self._r(value.get("nick", "{target}"), ctx)
            reason = self._r(value.get("reason", ""), ctx)
        else:
            nick, reason = self._r(value or "{target}", ctx), ""
        if not channel or not nick:
            log.warning("kick: canale o nick mancante")
            return
        if self._is_me(nick):
            return
        self.send(build_line("KICK", channel, nick, reason) if reason else build_line("KICK", channel, nick))

    def _ban_mask(self, value: Any, ctx: dict) -> str:
        if isinstance(value, dict):
            raw = self._r(value.get("mask") or value.get("nick") or "{target}", ctx)
        else:
            raw = self._r(value or "{target}", ctx)
        if not raw or "!" in raw or "@" in raw:
            return raw
        known = self.hosts.get(irc_lower(raw))
        if known and "@" in known:
            return "*!*@" + known.split("@", 1)[1]
        return f"{raw}!*@*"

    def _action_ban(self, value: Any, ctx: dict) -> None:
        channel, mask = self._channel_of(value, ctx), self._ban_mask(value, ctx)
        if channel and mask:
            self.send(build_line("MODE", channel, "+b", mask))

    def _action_unban(self, value: Any, ctx: dict) -> None:
        channel, mask = self._channel_of(value, ctx), self._ban_mask(value, ctx)
        if channel and mask:
            self.send(build_line("MODE", channel, "-b", mask))

    def _action_kickban(self, value: Any, ctx: dict) -> None:
        self._action_ban(value, ctx)
        self._action_kick(value, ctx)

    def _prefix_mode(self, mode: str, value: Any, ctx: dict) -> None:
        channel = self._channel_of(value, ctx)
        if isinstance(value, dict):
            nick = self._r(value.get("nick", "{target}"), ctx)
        else:
            nick = self._r(value or "{target}", ctx)
        if channel and nick:
            self.send(build_line("MODE", channel, mode, nick))

    def _action_op(self, value, ctx): self._prefix_mode("+o", value, ctx)
    def _action_deop(self, value, ctx): self._prefix_mode("-o", value, ctx)
    def _action_halfop(self, value, ctx): self._prefix_mode("+h", value, ctx)
    def _action_dehalfop(self, value, ctx): self._prefix_mode("-h", value, ctx)
    def _action_voice(self, value, ctx): self._prefix_mode("+v", value, ctx)
    def _action_devoice(self, value, ctx): self._prefix_mode("-v", value, ctx)

    def _action_mode(self, value: Any, ctx: dict) -> None:
        if isinstance(value, dict):
            target = self._r(value.get("target"), ctx) or ctx.get("channel", "")
            modes = self._r(value.get("modes"), ctx)
            line = f"MODE {target} {modes}"
        else:
            line = f"MODE {self._r(value, ctx)}"
        self.send(sanitize(line).strip())

    def _action_topic(self, value: Any, ctx: dict) -> None:
        channel = self._channel_of(value, ctx)
        text = self._r(value.get("text") if isinstance(value, dict) else value, ctx)
        if channel:
            self.send(build_line("TOPIC", channel, text))

    def _action_invite(self, value: Any, ctx: dict) -> None:
        channel = self._channel_of(value, ctx)
        nick = self._r(value.get("nick", "{target}") if isinstance(value, dict) else (value or "{target}"), ctx)
        if channel and nick:
            self.send(build_line("INVITE", nick, channel))

    def _action_join(self, value: Any, ctx: dict) -> None:
        if isinstance(value, dict):
            channel, key = self._r(value.get("channel"), ctx), self._r(value.get("key"), ctx)
        else:
            channel, _, key = self._r(value or "{arg1}", ctx).partition(" ")
        if channel:
            self.send(build_line("JOIN", channel, key) if key else build_line("JOIN", channel))

    def _action_part(self, value: Any, ctx: dict) -> None:
        if isinstance(value, dict):
            channel, reason = self._channel_of(value, ctx), self._r(value.get("reason"), ctx)
        else:
            channel, reason = self._r(value, ctx) or ctx.get("channel", ""), ""
        if channel:
            self.send(build_line("PART", channel, reason) if reason else build_line("PART", channel))

    def _action_nick(self, value: Any, ctx: dict) -> None:
        nick = self._r(value, ctx).split(" ")[0]
        if nick:
            self.send(build_line("NICK", nick))

    def _action_raw(self, value: Any, ctx: dict) -> None:
        line = self._r(value, ctx)
        if line:
            self.send(sanitize(line))

    def _action_help(self, value: Any, ctx: dict) -> None:
        method = value.get("method", "notice") if isinstance(value, dict) else (value or "notice")
        prefix = ctx.get("prefix", "")
        wanted = (ctx.get("arg1") or "").lstrip(prefix).lower()
        is_admin = self.is_admin(ctx.get("mask", ""))
        visible = [c for c in self.config.commands.values()
                   if c.enabled and not c.hidden
                   and self.has_permission(ctx.get("mask", ""), c.permission, ctx.get("channel", ""), is_admin)]
        if wanted:
            cmd = self.config.find_command(wanted)
            if cmd is None or cmd not in visible:
                text = f"Comando sconosciuto: {wanted}"
            else:
                text = f"{prefix}{cmd.name} {cmd.usage}".strip()
                if cmd.description:
                    text += f" — {cmd.description}"
                if cmd.aliases:
                    text += f" (alias: {', '.join(prefix + a for a in cmd.aliases)})"
        else:
            names = ", ".join(prefix + c.name for c in sorted(visible, key=lambda c: c.name))
            text = f"Comandi disponibili: {names}. Usa {prefix}help <comando> per i dettagli."
        if method == "say":
            self.say(ctx["_target"], text)
        else:
            self.notice(ctx.get("nick") or ctx["_target"], text)

    def _action_reload(self, value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        target = ctx.get("nick") or ctx["_target"]
        try:
            self.reload()
        except ConfigError as exc:
            log.error("Ricaricamento fallito: %s", exc)
            self.notice(target, render(opts.get("error", "Errore nella configurazione: {error}"),
                                       {**ctx, "error": str(exc)}))
            return
        self.notice(target, render(opts.get("ok", "Configurazione ricaricata."), ctx))

    def _action_quit(self, value: Any, ctx: dict) -> None:
        self._quitting = True
        reason = self._r(value, ctx) or "Arrivederci!"
        self.send(build_line("QUIT", reason))

    # ------------------------------------------------------------------ reload

    def reload(self) -> None:
        """Ricarica il file YAML. Server e identità richiedono un riavvio per cambiare."""
        if self.config.path is None:
            raise ConfigError("nessun file di configurazione da ricaricare")
        new = load_config(self.config.path)
        self.config = new
        log.info("Configurazione ricaricata da %s", new.path)
        if self.registered:
            self._spawn(self._join_channels())

    def stop(self) -> None:
        self._quitting = True
        if self._writer is not None:
            self._write(self._writer, build_line("QUIT", "Arrivederci!"))


def _redact(line: str) -> str:
    """Nasconde le password nei log."""
    upper = line.upper()
    if upper.startswith(("PASS ", "OPER ", "AUTHENTICATE ")) and not upper.startswith("AUTHENTICATE PLAIN"):
        return line.split(" ", 1)[0] + " ********"
    if re.match(r"(?i)^PRIVMSG \S*serv :?(IDENTIFY|LOGIN)\b", line):
        return line.split(":", 1)[0] + ":******** (identificazione)"
    return line
