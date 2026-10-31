"""Karma del canale: ``nick++`` / ``nick--``, punteggi permanenti, classifica, azzeramento al ban."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .irc import Prefix, irc_lower, mask_match
from .memory import Memory, format_duration
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.karma")

# "mario++", "anna--", anche più d'uno: "mario++ anna++". Non dentro parole o codice come "a+++b".
_PLUSPLUS = re.compile(r"(?:^|(?<=[\s,;(]))(?P<nick>[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]*?)"
                       r"(?P<op>\+\+|--)(?=$|[\s,.;:!?)])")


class KarmaMixin:
    @property
    def karma_store(self: "Bot") -> Memory | None:
        return self.memory if self.config.karma.enabled else None

    def _kt(self: "Bot", key: str, ctx: dict, **extra: Any) -> str:
        if "score" in extra:  # "1 punto", "-1 punto", "12 punti"
            extra["points"] = f"{extra['score']} {'punto' if abs(int(extra['score'])) == 1 else 'punti'}"
        return render(self.config.karma.texts[key], {**ctx, **extra})

    def _karma_scope(self: "Bot", channel: str) -> str:
        return "" if self.config.karma.scope == "global" else irc_lower(channel)

    def _karma_where(self: "Bot", channel: str) -> str:
        return "" if self.config.karma.scope == "global" or not channel else f" in {channel}"

    def _karma_enabled_in(self: "Bot", channel: str) -> bool:
        cfg = self.config.karma
        return (self.karma_store is not None and bool(channel)
                and (not cfg.channels or self._channel_in(channel, cfg.channels)))

    # ------------------------------------------------------------------ nick++ / nick--

    def karma_from_message(self: "Bot", text: str, ctx: dict) -> bool:
        """Riconosce ``nick++``/``nick--`` in un messaggio di canale. True se ha gestito qualcosa."""
        cfg = self.config.karma
        channel = ctx.get("channel", "")
        if not cfg.plusplus or not self._karma_enabled_in(channel):
            return False
        handled = False
        for m in list(_PLUSPLUS.finditer(text))[: cfg.max_per_message]:
            delta = 1 if m.group("op") == "++" else -1
            if delta < 0 and not cfg.allow_negative:
                continue
            handled = self.give_karma(m.group("nick"), delta, ctx, reason="utente") or handled
        return handled

    def give_karma(self: "Bot", target: str, delta: int, ctx: dict, reason: str = "utente",
                   quiet: bool = False) -> bool:
        """Applica un cambio di karma con tutte le regole (niente auto-karma, cooldown, limite giornaliero)."""
        store, cfg = self.karma_store, self.config.karma
        channel, giver = ctx.get("channel", ""), ctx.get("nick", "")
        if store is None or not channel or not target:
            return False
        reply_to = ctx.get("_target", channel)
        if target.lower() in cfg.ignore_words:
            return False
        chan = self.channels.get(irc_lower(channel))
        present = chan is not None and irc_lower(target) in chan.users
        if cfg.require_present and not present and not self._is_me(target):
            return False  # "c++", "g++", "grazie mille" o un nick che non c'è: non è karma
        if self._is_me(target):
            self.say(reply_to, self._kt("bot", ctx, target=target))
            return True
        if irc_lower(target) == irc_lower(giver):
            self.say(reply_to, self._kt("self", ctx, target=target))
            return True
        scope = self._karma_scope(channel)
        now = self.wallclock()
        last = store.karma_last_given(scope, giver, target)
        if cfg.cooldown > 0 and last is not None and now - last < cfg.cooldown:
            self.notice(giver, self._kt("cooldown", ctx, target=target,
                                        wait=format_duration(cfg.cooldown - (now - last))))
            return True
        if cfg.daily_limit > 0 and store.karma_given_since(giver, now - 86400) >= cfg.daily_limit:
            self.notice(giver, self._kt("daily_limit", ctx, limit=cfg.daily_limit))
            return True
        # usa il nick come lo scrive il server, se l'utente è noto
        mask = self.hosts.get(irc_lower(target), "")
        display = Prefix.parse(mask).nick if mask else target
        row = store.karma_change(scope, display, delta, giver, mask, reason)
        log.info("Karma %s%d a %s da %s in %s (ora %d)", "+" if delta > 0 else "", delta, display, giver,
                 channel, row.score)
        if cfg.announce and not quiet:
            self.say(reply_to, self._kt("changed", ctx, target=display, score=row.score,
                                        sign="+" if delta > 0 else "-", delta=delta))
        return True

    # ------------------------------------------------------------------ azioni

    def _karma_target(self: "Bot", value: Any, ctx: dict, default: str = "{target}") -> str:
        raw = value.get("nick", default) if isinstance(value, dict) else (value or default)
        return self._r(raw, ctx).strip().strip(":,").rstrip("+-")

    def _action_karma_change(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {"nick": value}
        target = self._karma_target(opts, ctx)
        try:
            delta = int(self._r(opts.get("delta", 1), ctx))
        except ValueError:
            delta = 1
        delta = max(-1, min(1, delta)) or 1
        self.give_karma(target, delta, ctx, reason="utente", quiet=bool(opts.get("quiet", False)))

    def _action_karma_show(self: "Bot", value: Any, ctx: dict) -> None:
        store = self.karma_store
        if store is None:
            return
        target = self._karma_target(value, ctx)
        if target.lower() in ("top", "classifica"):  # "!karma top"
            self._action_karma_top(None, ctx)
            return
        channel = ctx.get("channel", "")
        scope = self._karma_scope(channel)
        if not channel and self.config.karma.scope == "channel":
            channel = next(iter(self.config.karma.channels), "") or next(
                (c.name for c in self.channels.values()), "")
            scope = irc_lower(channel)
        row = store.karma_get(scope, target)
        if row is None or (row.score == 0 and row.plus == 0 and row.minus == 0):
            self.say(ctx["_target"], self._kt("show_none", ctx, target=target))
            return
        pos, total = store.karma_rank(scope, target)
        rank = self._kt("show_rank", ctx, rank=pos, total=total, where=self._karma_where(channel)) if pos else ""
        self.say(ctx["_target"], self._kt("show", ctx, target=row.nick, score=row.score, plus=row.plus,
                                          minus=row.minus, rank=rank))

    def _action_karma_top(self: "Bot", value: Any, ctx: dict) -> None:
        store = self.karma_store
        if store is None:
            return
        opts = value if isinstance(value, dict) else {}
        channel = ctx.get("channel", "") or next((c.name for c in self.channels.values()), "")
        try:
            size = int(self._r(opts.get("size", self.config.karma.top_size), ctx))
        except ValueError:
            size = self.config.karma.top_size
        rows = store.karma_top(self._karma_scope(channel), max(1, min(size, 25)), worst=bool(opts.get("worst")))
        where = self._karma_where(channel)
        if not rows:
            self.say(ctx["_target"], self._kt("top_empty", ctx, where=where))
            return
        items = ", ".join(self._kt("top_item", ctx, pos=i, nick=r.nick, score=r.score)
                          for i, r in enumerate(rows, start=1))
        self.say(ctx["_target"], self._kt("top", ctx, where=where, list=items))

    def _action_karma_reset(self: "Bot", value: Any, ctx: dict) -> None:
        store = self.karma_store
        if store is None:
            return
        target = self._karma_target(value, ctx, "{arg1}")
        if not target:
            return
        old = store.karma_reset(self._karma_scope(ctx.get("channel", "")), target, ctx.get("nick", ""), "reset")
        if old is None:
            self.say(ctx["_target"], self._kt("reset_none", ctx, target=target))
        else:
            log.info("Karma di %s azzerato da %s (era %d)", target, ctx.get("nick"), old)
            self.say(ctx["_target"], self._kt("reset", ctx, target=target, old=old))

    # ------------------------------------------------------------------ azzeramento al ban

    def _karma_on_ban(self: "Bot", channel: str, mask: str, setter: str) -> None:
        """Un ban (+b) nel canale azzera il karma di chi ne è colpito: utenti presenti e nick noti."""
        store, cfg = self.karma_store, self.config.karma
        if store is None or not cfg.reset_on_ban or not self._karma_enabled_in(channel):
            return
        scope = self._karma_scope(channel)
        hit: dict[str, str] = {}
        chan = self.channels.get(irc_lower(channel))
        for key in (chan.users if chan else {}):
            full = self.hosts.get(key, "")
            if full and mask_match(mask, full):
                hit[key] = full
        for row in store.karma_all(scope):
            key = irc_lower(row.nick)
            full = row.mask or f"{row.nick}!*@*"
            if key not in hit and (mask_match(mask, full) or mask_match(mask, f"{row.nick}!*@*")):
                hit[key] = full
        for key, full in hit.items():
            if self._is_me(key) or self.is_immune(full, channel):
                continue
            nick = Prefix.parse(full).nick or key
            old = store.karma_reset(scope, nick, setter or "?", "ban")
            if old:
                log.info("Karma di %s azzerato per il ban %s in %s (era %d)", nick, mask, channel, old)
                if cfg.announce_reset:
                    self.say(channel, self._kt("reset_on_ban", {"channel": channel}, target=nick, old=old))
