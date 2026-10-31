"""Gestione dei ban: ban a tempo, registro persistente, enforcement e sincronizzazione col server.

È un mixin di :class:`linuxbot.bot.Bot`: usa il suo stato (canali, host noti, memoria) e i suoi
metodi di invio. Senza ``bans.enabled`` le azioni ban/unban/kickban funzionano lo stesso, ma
senza registro né scadenze.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .config import BAN_MASKS
from .irc import Prefix, build_line, irc_lower, mask_match
from .memory import Ban, Memory, format_duration, format_ts, parse_duration, time_ago
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot")


class BanMixin:
    # attributi forniti da Bot
    _banlist_buffer: dict[str, list[tuple[str, str, float | None]]]
    _ban_resync: dict[str, list[Ban]]

    # ------------------------------------------------------------------ utilità

    @property
    def ban_store(self: "Bot") -> Memory | None:
        return self.memory if self.config.bans.enabled else None

    def _bt(self: "Bot", key: str, ctx: dict, **extra: Any) -> str:
        return render(self.config.bans.texts[key], {**ctx, **extra})

    def _ban_reply(self: "Bot", ctx: dict, text: str) -> None:
        """Le conferme dei ban vanno in notice a chi ha dato il comando."""
        target = ctx.get("nick") or ctx.get("_target")
        if target and text:
            self.notice(target, text)

    def _expiry(self: "Bot", ban: Ban | None = None, expires: float | None = None, ctx: dict | None = None) -> str:
        expires = ban.expires if ban is not None else expires
        if not expires:
            return self._bt("expiry_permanent", ctx or {})
        remaining = format_duration(max(0.0, expires - self.wallclock()))
        return self._bt("expiry_in", ctx or {}, remaining=remaining)

    def _known_mask(self: "Bot", nick: str) -> str:
        """Ultima maschera nick!user@host nota: utenti presenti o, se usciti, quella in memoria."""
        known = self.hosts.get(irc_lower(nick), "")
        if not known and self.memory is not None:
            entry = self.memory.last_seen(nick)
            if entry is not None and entry.mask:
                known = entry.mask
        return known

    def _mask_for_nick(self: "Bot", nick: str, template: str | None = None) -> str:
        known = self._known_mask(nick)
        if "@" not in known:
            return f"{nick}!*@*"
        p = Prefix.parse(known)
        ident = "*" + p.user.lstrip("~") if p.user else "*"
        return render(template or self.config.bans.mask_template,
                      {"nick": p.nick or nick, "user": p.user or "*", "ident": ident, "host": p.host or "*"})

    @staticmethod
    def _is_mask(text: str) -> bool:
        return "!" in text or "@" in text or "*" in text

    def _is_op_in(self: "Bot", channel: str) -> bool:
        modes = self.user_modes(channel, self.nick)
        need = self.prefix_modes.index("h") if "h" in self.prefix_modes else self.prefix_modes.index("o")
        return self._rank(modes) <= need

    def _protected(self: "Bot", mask: str) -> bool:
        """True se la maschera colpirebbe il bot stesso o un admin del bot (presente o noto)."""
        own = self.hosts.get(irc_lower(self.nick), f"{self.nick}!*@*")
        if mask_match(mask, own) or self.is_admin(mask):
            return True
        return any(mask_match(mask, full) and self.is_admin(full) for full in self.hosts.values())

    def _users_matching(self: "Bot", channel: str, mask: str) -> list[str]:
        chan = self.channels.get(irc_lower(channel))
        if chan is None:
            return []
        result = []
        for key in chan.users:
            full = self.hosts.get(key)
            if full and mask_match(mask, full):
                result.append(Prefix.parse(full).nick)
        return result

    # ------------------------------------------------------------------ azioni

    def _ban_common(self: "Bot", value: Any, ctx: dict, kick: bool) -> None:
        opts = value if isinstance(value, dict) else {"nick": value or "{target}"}
        bans_cfg = self.config.bans
        raw = self._r(opts.get("mask") or opts.get("nick") or "{target}", ctx).strip()
        channel = self._channel_of(opts, ctx)
        if not channel:
            self._ban_reply(ctx, self._bt("no_channel", ctx))
            return
        if not raw:
            return
        if self._is_mask(raw):
            mask, nick = raw, ""
        else:
            nick = raw
            mask_type = opts.get("mask_type")
            template = BAN_MASKS.get(str(mask_type), str(mask_type)) if mask_type else None
            mask = self._mask_for_nick(nick, template)
        if self._is_me(nick) or self._protected(mask):
            log.warning("Rifiuto di bannare %s (%s): è il bot o un admin", nick or mask, mask)
            return

        dur_text = self._r(opts.get("duration", ""), ctx).strip() or bans_cfg.default_duration
        seconds = parse_duration(dur_text) if dur_text else 0.0
        if seconds is None:
            self._ban_reply(ctx, self._bt("bad_duration", ctx, duration=dur_text))
            return
        max_s = parse_duration(bans_cfg.max_duration) if bans_cfg.max_duration else 0.0
        if max_s and (seconds == 0 or seconds > max_s):
            self._ban_reply(ctx, self._bt("too_long", ctx, max=format_duration(max_s)))
            return
        expires = self.wallclock() + seconds if seconds else None
        duration = format_duration(seconds) if seconds else self._bt("expiry_permanent", ctx)
        reason = self._r(opts.get("reason", ""), ctx).strip() or self._bt("default_reason", ctx)

        self.send(build_line("MODE", channel, "+b", mask))
        if self.ban_store is not None:
            self.ban_store.add_ban(channel, mask, nick, ctx.get("nick", ""), reason, expires)
        log.info("Ban %s in %s (%s) da %s: %s", mask, channel, duration, ctx.get("nick"), reason)

        if kick:
            victims = {irc_lower(n): n for n in self._users_matching(channel, mask)}
            chan = self.channels.get(irc_lower(channel))
            if nick and (chan is None or irc_lower(nick) in chan.users):
                victims.setdefault(irc_lower(nick), nick)
            kick_reason = self._bt("kick_reason_temp" if seconds else "kick_reason", ctx,
                                   reason=reason, duration=duration)
            for victim in victims.values():
                full = self.hosts.get(irc_lower(victim), f"{victim}!*@*")
                if not self._is_me(victim) and not self.is_admin(full):
                    self.send(build_line("KICK", channel, victim, kick_reason))
        if opts.get("confirm"):
            self._ban_reply(ctx, self._bt("ban_done", ctx, mask=mask, channel=channel,
                                          expiry=self._expiry(expires=expires, ctx=ctx), duration=duration))

    def _action_ban(self: "Bot", value: Any, ctx: dict) -> None:
        self._ban_common(value, ctx, kick=False)

    def _action_kickban(self: "Bot", value: Any, ctx: dict) -> None:
        self._ban_common(value, ctx, kick=True)

    def _find_ban_targets(self: "Bot", raw: str, channel: str | None, active: bool = True) -> list[Ban]:
        store = self.ban_store
        if store is None:
            return []
        if raw.startswith("#") and raw[1:].isdigit():
            ban = store.get_ban(int(raw[1:]))
            return [ban] if ban and (ban.active or not active) else []
        if self._is_mask(raw):
            return store.find_bans(channel, mask=raw, active=active)
        found = {b.id: b for b in store.find_bans(channel, nick=raw, active=active)}
        known = self._known_mask(raw)
        if "@" in known:  # ban messi a mano (senza nick) che colpiscono il suo host
            for b in store.find_bans(channel, active=active):
                if mask_match(b.mask, known):
                    found.setdefault(b.id, b)
        return sorted(found.values(), key=lambda b: b.ts, reverse=not active)

    def _action_unban(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {"nick": value or "{target}"}
        raw = self._r(opts.get("mask") or opts.get("nick") or "{target}", ctx).strip()
        channel = self._channel_of(opts, ctx)
        if not raw:
            return
        bans = self._find_ban_targets(raw, channel or None)
        if not channel and bans:
            channel = bans[0].channel
        if not channel:
            self._ban_reply(ctx, self._bt("no_channel", ctx))
            return
        if bans:
            for ban in bans:
                self.ban_store.remove_ban(ban.id, ctx.get("nick", ""))  # type: ignore[union-attr]
                self.send(build_line("MODE", ban.channel, "-b", ban.mask))
                log.info("Unban %s in %s da %s", ban.mask, ban.channel, ctx.get("nick"))
                if self.ban_store is not None:
                    self._ban_reply(ctx, self._bt("unban_done", ctx, mask=ban.mask, channel=ban.channel))
            return
        mask = raw if self._is_mask(raw) else self._mask_for_nick(raw)
        self.send(build_line("MODE", channel, "-b", mask))
        if self.ban_store is not None:
            self._ban_reply(ctx, self._bt("unban_unknown", ctx, who=raw, mask=mask, channel=channel))

    def _ban_vars(self: "Bot", ban: Ban, ctx: dict) -> dict:
        now = self.wallclock()
        return dict(id=ban.id, mask=ban.mask, channel=ban.channel, setter=ban.setter or "?",
                    who=f" ({ban.nick})" if ban.nick else "", why=f": {ban.reason}" if ban.reason else "",
                    reason=ban.reason, ago=time_ago(ban.ts, now), when=format_ts(ban.ts),
                    expiry=self._expiry(ban, ctx=ctx))

    def _action_banlist(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {}
        arg = self._r(opts.get("channel", "{arg1}"), ctx).strip()
        channel = arg if arg.startswith(("#", "&")) else ctx.get("channel", "")
        if not channel:
            self._ban_reply(ctx, self._bt("no_channel", ctx))
            return
        bans = self.ban_store.find_bans(channel) if self.ban_store is not None else []
        if not bans:
            self._ban_reply(ctx, self._bt("list_empty", ctx, channel=channel))
            return
        limit = self.config.bans.list_max
        self._ban_reply(ctx, self._bt("list_header", ctx, channel=channel, count=len(bans)))
        for ban in bans[:limit]:
            self._ban_reply(ctx, self._bt("list_item", ctx, **self._ban_vars(ban, ctx)))
        if len(bans) > limit:
            self._ban_reply(ctx, self._bt("list_more", ctx, count=len(bans) - limit))

    def _action_baninfo(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {"nick": value or "{arg1}"}
        raw = self._r(opts.get("nick") or opts.get("mask") or "{arg1}", ctx).strip()
        if not raw:
            return
        channel = self._r(opts.get("channel"), ctx) if opts.get("channel") else ctx.get("channel", "")
        if raw.startswith("#") and raw[1:].isdigit():
            channel = ""  # un id è univoco
        bans = self._find_ban_targets(raw, channel or None)
        if bans:
            for ban in bans[: self.config.bans.list_max]:
                self._ban_reply(ctx, self._bt("info", ctx, **self._ban_vars(ban, ctx)))
            return
        old = self._find_ban_targets(raw, channel or None, active=False)
        if old and old[0].removed_ts:
            ban = old[0]
            self._ban_reply(ctx, self._bt("info_removed", ctx, **{**self._ban_vars(ban, ctx), "who": raw},
                                          removed_by=ban.removed_by or "?",
                                          removed_how="per scadenza" if ban.removed_by == "scadenza"
                                          else f"da {ban.removed_by or '?'}",
                                          removed_ago=time_ago(ban.removed_ts, self.wallclock())))
            return
        self._ban_reply(ctx, self._bt("info_none", ctx, who=raw))

    # ------------------------------------------------------------------ enforcement

    def _ban_on_join(self: "Bot", nick: str, mask: str, channel: str) -> bool:
        """Chi entra ed è bannato (il ban era stato perso dal server) viene espulso. True se espulso."""
        store = self.ban_store
        if store is None or not self.config.bans.enforce or self.is_admin(mask):
            return False
        for ban in store.find_bans(channel):
            if mask_match(ban.mask, mask) and not (ban.expires and ban.expires <= self.wallclock()):
                log.info("%s è bannato in %s (%s): lo espello", nick, channel, ban.mask)
                self.send(build_line("MODE", channel, "+b", ban.mask))
                self.send(build_line("KICK", channel, nick,
                                     self._bt("enforce_reason", {}, why=f": {ban.reason}" if ban.reason else "")))
                return True
        return False

    def _ban_on_mode(self: "Bot", setter: str, channel: str, adding: bool, mask: str) -> None:
        store = self.ban_store
        if store is None or self._is_me(setter):
            return
        existing = store.find_bans(channel, mask=mask)
        if adding:
            if not existing and self.config.bans.track_manual:
                store.add_ban(channel, mask, "", setter, "", None, manual=True)
                log.info("Registrato ban manuale %s in %s da %s", mask, channel, setter)
            return
        for ban in existing:
            if self.config.bans.enforce and not ban.manual:
                log.warning("%s ha tolto il ban %s in %s: lo rimetto (usa unban del bot)", setter, mask, channel)
                self.send(build_line("MODE", channel, "+b", mask))
            else:
                store.remove_ban(ban.id, setter)

    def _ban_sync_start(self: "Bot", channel: str) -> None:
        if self.ban_store is not None:
            self._banlist_buffer[irc_lower(channel)] = []
            self.send(build_line("MODE", channel, "+b"))

    def _ban_list_entry(self: "Bot", params: list[str]) -> None:
        """367 RPL_BANLIST: <me> <canale> <maschera> [<chi> <timestamp>]"""
        if len(params) < 3:
            return
        buf = self._banlist_buffer.get(irc_lower(params[1]))
        if buf is not None:
            setter = Prefix.parse(params[3]).nick if len(params) > 3 else ""
            ts = float(params[4]) if len(params) > 4 and params[4].isdigit() else None
            buf.append((params[2], setter, ts))

    def _ban_list_end(self: "Bot", channel: str) -> None:
        """368 fine lista: confronta i ban sul server con il registro."""
        buf = self._banlist_buffer.pop(irc_lower(channel), None)
        store = self.ban_store
        if buf is None or store is None:
            return
        on_server = {irc_lower(m) for m, _, _ in buf}
        known = {irc_lower(b.mask) for b in store.find_bans(channel)}
        if self.config.bans.track_manual:
            for mask, setter, ts in buf:
                if irc_lower(mask) not in known:
                    store.add_ban(channel, mask, "", setter, "", None, manual=True, ts=ts)
        if self.config.bans.enforce:
            now = self.wallclock()
            missing = [b for b in store.find_bans(channel)
                       if irc_lower(b.mask) not in on_server and not (b.expires and b.expires <= now)]
            if missing:
                self._ban_resync[irc_lower(channel)] = missing
                if self._is_op_in(channel):
                    self._ban_resync_apply(channel)

    def _ban_resync_apply(self: "Bot", channel: str) -> None:
        for ban in self._ban_resync.pop(irc_lower(channel), []):
            log.info("Ripristino il ban %s in %s", ban.mask, channel)
            self.send(build_line("MODE", channel, "+b", ban.mask))

    def check_expired_bans(self: "Bot") -> int:
        """Toglie i ban scaduti nei canali dove il bot è op. Restituisce quanti ne ha tolti."""
        store = self.ban_store
        if store is None:
            return 0
        removed = 0
        for ban in store.expired_bans():
            if irc_lower(ban.channel) in self.channels and self._is_op_in(ban.channel):
                store.remove_ban(ban.id, "scadenza")
                self.send(build_line("MODE", ban.channel, "-b", ban.mask))
                log.info("Ban %s in %s scaduto", ban.mask, ban.channel)
                removed += 1
        return removed

    async def _ban_expiry_loop(self: "Bot") -> None:
        while True:
            await asyncio.sleep(max(1.0, self.config.bans.check_interval))
            try:
                self.check_expired_bans()
            except Exception:
                log.exception("Errore controllando i ban scaduti")

