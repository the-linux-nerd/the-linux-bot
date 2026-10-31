"""Attività programmate (sezione ``schedules``): azioni a orari fissi ("at") o a intervalli ("every")."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import ScheduleConfig
from .memory import format_duration
from .irc import irc_lower

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.events")



def _fmt(seconds: float) -> str:
    return format_duration(seconds) if seconds >= 60 else "1 minuto"


class SchedulerMixin:
    _schedule_last: dict[str, dict]  # stato delle attività se non c'è un database

    def now_local(self: "Bot") -> datetime:
        """Ora corrente nel fuso orario configurato (``behavior.timezone``) o in quello del sistema."""
        tz_name = self.config.behavior.timezone
        if tz_name:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(self.wallclock(), ZoneInfo(tz_name))
        return datetime.fromtimestamp(self.wallclock()).astimezone()

    def _schedule_channels(self: "Bot", sched: ScheduleConfig, warn: bool = True) -> list[str]:
        names = sched.channels or [c.name for c in self.config.channels]
        present = [n for n in names if irc_lower(n) in self.channels]
        if warn:
            for missing in set(names) - set(present):
                log.warning("Attività '%s': non sono in %s, salto", sched.name, missing)
        return present

    def run_schedule(self: "Bot", sched: ScheduleConfig, reason: str = "orario", late: str = "",
                     warn: bool = True) -> int:
        """Esegue le azioni dell'attività in ognuno dei suoi canali. Restituisce in quanti canali."""
        if not sched.per_channel:
            ctx = self._base_context(None, "")
            ctx.update(nick=self.nick, schedule=sched.name, late=late, _source=f"attività {sched.name}")
            self.run_actions(sched.actions, ctx)
            log.info("Attività '%s' eseguita (%s)", sched.name, reason)
            return 1
        channels = self._schedule_channels(sched, warn)
        for channel in channels:
            ctx = self._base_context(None, channel)
            ctx["nick"] = self.nick
            ctx["schedule"] = sched.name
            ctx["late"] = late
            ctx["_source"] = f"attività {sched.name}"
            self.run_actions(sched.actions, ctx)
        if channels:
            log.info("Attività '%s' eseguita (%s) in %s", sched.name, reason, ", ".join(channels))
        return len(channels)

    # ------------------------------------------------------------------ stato (nel database se c'è)

    def _schedule_state(self: "Bot", name: str) -> dict:
        if self.memory is not None:
            return self.memory.state_get(f"schedule:{name}", {}) or {}
        return self._schedule_last.get(name, {})  # type: ignore[return-value]

    def _schedule_save(self: "Bot", name: str, state: dict) -> None:
        if self.memory is not None:
            self.memory.state_set(f"schedule:{name}", state)
        else:
            self._schedule_last[name] = state  # type: ignore[assignment]

    @staticmethod
    def last_occurrence(sched: ScheduleConfig, now: datetime) -> datetime | None:
        """L'ultimo orario previsto dall'attività non successivo a ``now`` (guarda fino a 8 giorni indietro)."""
        for back in range(0, 8):
            day = (now - timedelta(days=back)).date()
            if sched.days and day.weekday() not in sched.days:
                continue
            for t in sorted(sched.at, reverse=True):
                hour, minute = (int(x) for x in t.split(":"))
                candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)
                if candidate <= now:
                    return candidate
        return None

    def run_due_schedules(self: "Bot", now: datetime | None = None) -> list[str]:
        """Esegue le attività il cui orario è arrivato (o è passato da meno di ``catch_up``).

        L'ultima esecuzione è salvata nel database: dopo un riavvio un'attività non viene ripetuta,
        e se il bot era spento all'orario previsto la recupera, entro ``catch_up``.
        """
        now = (now or self.now_local()).replace(second=0, microsecond=0)
        ran = []
        for sched in self.config.schedules:
            if not sched.enabled:
                continue
            state = self._schedule_state(sched.name)
            if sched.every:
                last = state.get("last_ts")
                if last is None or now.timestamp() - last >= sched.every:
                    if last is None:
                        self._schedule_save(sched.name, {"last_ts": now.timestamp()})  # si parte a contare da ora
                    elif self.run_schedule(sched, "intervallo"):
                        self._schedule_save(sched.name, {"last_ts": now.timestamp()})
                        ran.append(sched.name)
                continue
            occurrence = self.last_occurrence(sched, now)
            if occurrence is None:
                continue
            key = occurrence.isoformat()
            if state.get("last") == key:
                continue  # già fatta
            delay = (now - occurrence).total_seconds()
            if delay > max(sched.catch_up, 60):
                if state.get("last") is not None:
                    log.warning("Attività '%s' delle %s saltata: il bot non era attivo (recupero entro %s)",
                                sched.name, occurrence.strftime("%d/%m %H:%M"), _fmt(sched.catch_up))
                self._schedule_save(sched.name, {"last": key, "skipped": True})
                continue
            late = ""
            if delay >= 60:
                late = sched.late_text.replace("{scheduled}", occurrence.strftime("%H:%M"))
            # se il bot non è ancora nel canale (es. appena avviato) riprova al prossimo minuto
            if self.run_schedule(sched, "in ritardo" if late else "orario", late, warn=delay >= sched.catch_up - 60):
                self._schedule_save(sched.name, {"last": key, "ran_at": now.isoformat()})
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
        if not sched.per_channel:
            self.notice(target, f"Attività '{name}' eseguita.")
            return
        self.notice(target, f"Attività '{name}' eseguita in {where}." if count else
                    f"Attività '{name}': non sono in nessuno dei suoi canali.")
