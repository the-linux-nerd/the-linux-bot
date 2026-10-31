"""Attività programmate (sezione ``schedules``): azioni a orari fissi ("at") o a intervalli ("every")."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import ScheduleConfig
from .irc import irc_lower

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.events")

MAX_CATCH_UP_MINUTES = 10  # se il bot è rimasto bloccato qualche minuto, recupera gli orari saltati


class SchedulerMixin:
    _schedule_last: dict[str, str]
    _schedule_every_last: dict[str, float]
    _schedule_checked: datetime | None

    def now_local(self: "Bot") -> datetime:
        """Ora corrente nel fuso orario configurato (``behavior.timezone``) o in quello del sistema."""
        tz_name = self.config.behavior.timezone
        if tz_name:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(self.wallclock(), ZoneInfo(tz_name))
        return datetime.fromtimestamp(self.wallclock()).astimezone()

    def _schedule_channels(self: "Bot", sched: ScheduleConfig) -> list[str]:
        names = sched.channels or [c.name for c in self.config.channels]
        present = [n for n in names if irc_lower(n) in self.channels]
        for missing in set(names) - set(present):
            log.warning("Attività '%s': non sono in %s, salto", sched.name, missing)
        return present

    def run_schedule(self: "Bot", sched: ScheduleConfig, reason: str = "orario") -> int:
        """Esegue le azioni dell'attività in ognuno dei suoi canali. Restituisce in quanti canali."""
        channels = self._schedule_channels(sched)
        for channel in channels:
            ctx = self._base_context(None, channel)
            ctx["nick"] = self.nick
            ctx["schedule"] = sched.name
            ctx["_source"] = f"attività {sched.name}"
            self.run_actions(sched.actions, ctx)
        if channels:
            log.info("Attività '%s' eseguita (%s) in %s", sched.name, reason, ", ".join(channels))
        return len(channels)

    def run_due_schedules(self: "Bot", now: datetime | None = None) -> list[str]:
        """Controlla quali attività devono partire adesso (o nei minuti appena trascorsi) e le esegue."""
        now = (now or self.now_local()).replace(second=0, microsecond=0)
        last = self._schedule_checked
        if last is None or now <= last or now - last > timedelta(minutes=MAX_CATCH_UP_MINUTES):
            minutes = [now]
        else:
            minutes = [last + timedelta(minutes=i) for i in range(1, int((now - last).total_seconds() // 60) + 1)]
        self._schedule_checked = now
        ran = []
        mono = self.clock()
        for sched in self.config.schedules:
            if not sched.enabled:
                continue
            if sched.every:
                started = self._schedule_every_last.setdefault(sched.name, mono)
                if mono - started >= sched.every:
                    self._schedule_every_last[sched.name] = mono
                    self.run_schedule(sched, "intervallo")
                    ran.append(sched.name)
                continue
            for minute in minutes:
                key = minute.strftime("%Y-%m-%d %H:%M")
                if (minute.strftime("%H:%M") in sched.at and (not sched.days or minute.weekday() in sched.days)
                        and self._schedule_last.get(sched.name) != key):
                    self._schedule_last[sched.name] = key
                    self.run_schedule(sched)
                    ran.append(sched.name)
        return ran

    async def _scheduler_loop(self: "Bot") -> None:
        while True:
            now = self.now_local()
            await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.5)  # poco dopo l'inizio del minuto
            try:
                self.run_due_schedules()
            except Exception:
                log.exception("Errore nelle attività programmate")

    def _action_schedule_run(self: "Bot", value: Any, ctx: dict) -> None:
        name = self._r(value.get("name", "{arg1}") if isinstance(value, dict) else (value or "{arg1}"), ctx).strip()
        sched = next((s for s in self.config.schedules if s.name == name), None)
        target = ctx.get("nick") or ctx["_target"]
        if sched is None:
            names = ", ".join(s.name for s in self.config.schedules) or "nessuna"
            self.notice(target, f"Attività sconosciuta: {name or '?'}. Disponibili: {names}.")
            return
        count = self.run_schedule(sched, f"a mano da {ctx.get('nick', '?')}")
        where = "1 canale" if count == 1 else f"{count} canali"
        self.notice(target, f"Attività '{name}' eseguita in {where}." if count else
                    f"Attività '{name}': non sono in nessuno dei suoi canali.")
