import asyncio
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

ROME = ZoneInfo("Europe/Rome")

SCHED = """
memory: {database: ":memory:"}
behavior: {timezone: Europe/Rome}
karma: {enabled: true, cooldown: 0}
channels: ["#linux", "#altro"]
commands:
  esegui: {permission: admin, actions: [{schedule_run: "{arg1}"}]}
schedules:
  - name: karma-mezzanotte
    at: "00:00"
    channels: ["#linux"]
    actions:
      - karma_top: {size: 10, skip_empty: true, text: "Top ten{where}: {list}"}
  - name: weekend
    at: ["09:00", "21:30"]
    days: [sab, domenica]
    actions: [{say: "Buon weekend da {botnick} in {channel}!"}]
  - name: tip
    every: 2h
    channels: ["#altro"]
    actions: [{say: "consiglio"}]
"""


def at(*args):
    return datetime(*args, tzinfo=ROME)


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(SCHED)
        for chan in ("#linux", "#altro"):
            self.bot.handle_line(f":Bot!b@h JOIN {chan}")
            self.bot.handle_line(f":srv 353 Bot = {chan} :@Bot anna mario luigi")
        for giver, text in (("anna", "mario++"), ("luigi", "mario++"), ("mario", "luigi++")):
            self.bot.handle_line(f":{giver}!x@h PRIVMSG #linux :{text}")
        self.bot.take()

    async def run_at(self, when):
        ran = self.bot.run_due_schedules(when)
        await self.bot.drain()
        return ran, self.bot.take()

    async def test_midnight_top_ten(self):
        self.assertEqual(await self.run_at(at(2026, 11, 2, 23, 59)), ([], []))
        ran, out = await self.run_at(at(2026, 11, 3, 0, 0))
        self.assertEqual(ran, ["karma-mezzanotte"])
        self.assertEqual(out, ["PRIVMSG #linux :Top ten in #linux: 1. mario (2), 2. luigi (1)"])
        self.assertEqual(await self.run_at(at(2026, 11, 3, 0, 0)), ([], []))  # una volta sola
        ran, _ = await self.run_at(at(2026, 11, 4, 0, 0))                    # e di nuovo il giorno dopo
        self.assertIn("karma-mezzanotte", ran)

    async def test_catch_up_skipped_minutes(self):
        await self.run_at(at(2026, 11, 2, 23, 57))
        ran, _ = await self.run_at(at(2026, 11, 3, 0, 2))  # il ciclo è rimasto fermo 5 minuti
        self.assertEqual(ran, ["karma-mezzanotte"])

    async def test_no_catch_up_after_long_pause(self):
        await self.run_at(at(2026, 11, 2, 20, 0))
        ran, _ = await self.run_at(at(2026, 11, 3, 0, 30))
        self.assertNotIn("karma-mezzanotte", ran)

    async def test_skip_empty(self):
        bot = make_bot(SCHED)
        bot.handle_line(":Bot!b@h JOIN #linux")
        self.assertEqual(bot.run_due_schedules(at(2026, 11, 3, 0, 0)), ["karma-mezzanotte"])
        await bot.drain()
        self.assertEqual(bot.take(), [])

    async def test_days_and_multiple_times_in_all_channels(self):
        self.assertEqual((await self.run_at(at(2026, 11, 6, 21, 30)))[0], [])  # venerdì
        ran, out = await self.run_at(at(2026, 11, 7, 21, 30))                 # sabato
        self.assertIn("weekend", ran)
        self.assertIn("PRIVMSG #altro :Buon weekend da Bot in #altro!", out)
        self.assertIn("PRIVMSG #linux :Buon weekend da Bot in #linux!", out)
        self.assertIn("weekend", (await self.run_at(at(2026, 11, 8, 9, 0)))[0])  # domenica

    async def test_every_interval(self):
        self.assertEqual((await self.run_at(at(2026, 11, 3, 10, 0)))[0], [])
        self.assertEqual((await self.run_at(at(2026, 11, 3, 11, 59)))[0], [])
        ran, out = await self.run_at(at(2026, 11, 3, 12, 0))
        self.assertEqual((ran, out), (["tip"], ["PRIVMSG #altro consiglio"]))

    async def test_waits_for_channel_then_gives_up(self):
        self.bot.handle_line(":Bot!b@h PART #linux")
        self.assertEqual(await self.run_at(at(2026, 11, 3, 0, 0)), ([], []))  # riproverà
        self.bot.handle_line(":Bot!b@h JOIN #linux")                           # es. appena avviato
        ran, out = await self.run_at(at(2026, 11, 3, 0, 1))
        self.assertEqual(ran, ["karma-mezzanotte"])
        self.assertEqual(out, ["PRIVMSG #linux :Top ten in #linux: 1. mario (2), 2. luigi (1)"])
        self.bot.handle_line(":Bot!b@h PART #linux")
        await self.run_at(at(2026, 11, 3, 23, 50))
        with self.assertLogs("linuxbot.events", "WARNING"):
            await self.run_at(at(2026, 11, 4, 0, 9))   # ultimo minuto utile: avvisa
        with self.assertLogs("linuxbot.events", "WARNING") as logs:
            await self.run_at(at(2026, 11, 4, 0, 11))  # finestra scaduta
        self.assertIn("saltata", logs.output[-1])

    async def test_survives_restart_and_catches_up(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SCHED.replace('memory: {database: ":memory:"}', f"memory: {{database: '{os.path.join(tmp, 's.db')}'}}")
            cfg = cfg.replace('    at: "00:00"\n', '    at: "00:00"\n    catch_up: 12h\n')
            cfg = cfg.replace('text: "Top ten{where}: {list}"', 'text: "Top ten{where}{late}: {list}"')

            def start():
                bot = make_bot(cfg)
                bot.handle_line(":Bot!b@h JOIN #linux")
                bot.handle_line(":srv 353 Bot = #linux :@Bot anna mario")
                return bot

            bot = start()
            for t in ("anna!a@h PRIVMSG #linux :mario++",):
                bot.handle_line(":" + t)
            self.assertEqual(bot.run_due_schedules(at(2026, 11, 3, 0, 0)), ["karma-mezzanotte"])
            await bot.drain()
            bot.memory.close()
            bot = start()                                       # riavvio nello stesso minuto
            self.assertEqual(bot.run_due_schedules(at(2026, 11, 3, 0, 0)), [])
            bot.memory.close()
            # il PC era spento a mezzanotte: riacceso alle 8:15 recupera la top ten
            bot = start()
            bot.take()
            self.assertEqual(bot.run_due_schedules(at(2026, 11, 4, 8, 15)), ["karma-mezzanotte"])
            await bot.drain()
            self.assertEqual(bot.take(), ["PRIVMSG #linux :Top ten in #linux (prevista alle 00:00): 1. mario (1)"])
            self.assertEqual(bot.run_due_schedules(at(2026, 11, 4, 8, 16)), [])
            bot.memory.close()
            # acceso troppo tardi (oltre catch_up): niente
            bot = start()
            with self.assertLogs("linuxbot.events", "WARNING"):
                self.assertEqual(bot.run_due_schedules(at(2026, 11, 5, 13, 0)), [])
            bot.memory.close()

    async def test_every_survives_restart(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SCHED.replace('memory: {database: ":memory:"}', f"memory: {{database: '{os.path.join(tmp, 'e.db')}'}}")
            bot = make_bot(cfg)
            bot.handle_line(":Bot!b@h JOIN #altro")
            bot.run_due_schedules(at(2026, 11, 3, 10, 0))
            bot.memory.close()
            bot = make_bot(cfg)
            bot.handle_line(":Bot!b@h JOIN #altro")
            self.assertEqual(bot.run_due_schedules(at(2026, 11, 3, 11, 0)), [])
            self.assertIn("tip", bot.run_due_schedules(at(2026, 11, 3, 12, 0)))
            bot.memory.close()

    async def test_run_now_command(self):
        self.bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!esegui karma-mezzanotte")
        await self.bot.drain()
        out = self.bot.take()
        self.assertIn("PRIVMSG #linux :Top ten in #linux: 1. mario (2), 2. luigi (1)", out)
        self.assertIn("NOTICE boss :Attività 'karma-mezzanotte' eseguita in 1 canale.", out)
        self.bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!esegui boh")
        await self.bot.drain()
        self.assertIn("Attività sconosciuta: boh", self.bot.take()[0])

    async def test_loop_wakes_each_minute(self):
        from unittest import mock
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 1:
                raise asyncio.CancelledError

        calls = []

        def fake_now():  # prima chiamata: 23:59:50, poi è mezzanotte
            calls.append(1)
            return at(2026, 11, 2, 23, 59, 50) if len(calls) == 1 else at(2026, 11, 3, 0, 0, 1)

        with mock.patch.object(self.bot, "now_local", side_effect=fake_now), \
                mock.patch("linuxbot.scheduler.asyncio.sleep", fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await self.bot._scheduler_loop()
        self.assertAlmostEqual(sleeps[0], 10.5)
        await self.bot.drain()
        self.assertIn("Top ten in #linux", self.bot.take()[0])

    async def test_timezone(self):
        self.assertEqual(self.bot.now_local().tzinfo, ROME)
        utc_midnight_in_rome = datetime(2026, 11, 2, 23, 0, tzinfo=ZoneInfo("UTC")).astimezone(ROME)
        self.assertEqual((await self.run_at(utc_midnight_in_rome))[0], ["karma-mezzanotte"])

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        bad_schedules = [
            [{"name": "x", "at": "25:00", "actions": [{"say": "a"}]}],
            [{"name": "x", "at": "mezzanotte", "actions": [{"say": "a"}]}],
            [{"name": "x", "actions": [{"say": "a"}]}],
            [{"name": "x", "at": "00:00", "every": "1h", "actions": [{"say": "a"}]}],
            [{"name": "x", "every": "10s", "actions": [{"say": "a"}]}],
            [{"name": "x", "at": "00:00", "days": ["funday"], "actions": [{"say": "a"}]}],
            [{"name": "x", "at": "00:00"}],
            [{"name": "x", "at": "00:00", "actions": [{"say": "a"}]}, {"name": "x", "at": "01:00", "actions": [{"say": "a"}]}],
        ]
        for bad in bad_schedules:
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "schedules": bad})
        with self.assertRaises(ConfigError):
            parse_config({**base, "behavior": {"timezone": "Marte/Olympus"}})


if __name__ == "__main__":
    unittest.main()
