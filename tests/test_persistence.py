"""Ciò che il bot deve ricordare fra un avvio e l'altro, e le protezioni del database."""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from linuxbot.bot import Bot
from linuxbot.config import load_config
from linuxbot.memory import DatabaseLocked, Memory
from tests.helpers import BASE, RecordingBot

ROME = ZoneInfo("Europe/Rome")

CONFIG = {
    "behavior": {"timezone": "Europe/Rome"},
    "memory": {"enabled": True, "database": "dati/bot.db", "memos": {"delay": 0}},
    "karma": {"enabled": True},
    "bans": {"enabled": True},
    "commands": {"backup": {"permission": "admin", "actions": [{"db_backup": {"directory": "copie", "keep": 2}}]},
                 "lascia": {"actions": [{"memo_add": {"to": "{arg1}", "text": "{rest}"}}]},
                 "tempban": {"permission": "op", "actions": [{"kickban": {"nick": "{arg1}", "duration": "{arg2}"}}]}},
    "schedules": [{"name": "backup-notte", "at": "04:00", "catch_up": "1d", "per_channel": False,
                   "actions": [{"db_backup": {"directory": "copie", "keep": 2}}]}],
}


class PersistenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = dict(BASE)
        data.update(CONFIG)
        self.config_path = Path(self.tmp.name) / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        bot = RecordingBot(load_config(self.config_path))
        bot.registered = True
        bot.wallclock.now = datetime(2026, 11, 3, 12, 0, tzinfo=ROME).timestamp()
        bot.handle_line(":Bot!b@bot.host JOIN #linux")
        bot.handle_line(":srv 353 Bot = #linux :@Bot @op anna mario")
        bot.handle_line(":srv 368 Bot #linux :fine")
        for u in ("anna!a@casa.it", "mario!m@93-40.casa.it", "op!o@op.host"):
            bot.handle_line(f":{u} PRIVMSG #linux :ciao")
        bot.take()
        return bot

    async def test_everything_survives_a_restart(self):
        bot = self.start()
        db_file = Path(bot.memory.path)
        self.assertEqual(db_file, (Path(self.tmp.name) / "dati" / "bot.db").resolve())  # accanto al config
        bot.handle_line(":anna!a@casa.it PRIVMSG #linux :mario++")
        bot.handle_line(":anna!a@casa.it PRIVMSG #linux :!lascia luigi ciao luigi")
        bot.handle_line(":op!o@op.host PRIVMSG #linux :!tempban mario 2h")
        await bot.drain()
        bot.stop()  # chiusura pulita (anche Ctrl+C / SIGTERM passano da qui)

        bot = self.start()
        mem = bot.memory
        self.assertEqual(mem.karma_get("#linux", "mario").score, 1)
        self.assertEqual(mem.karma_last_given("#linux", "anna", "mario") is not None, True)  # cooldown ricordato
        self.assertEqual([m.text for m in mem.pending("luigi")], ["ciao luigi"])
        self.assertEqual(len(mem.find_bans("#linux")), 1)
        self.assertIsNotNone(mem.last_seen("anna"))
        bot.handle_line(":anna!a@casa.it PRIVMSG #linux :mario++")  # entro l'ora: ancora in cooldown
        self.assertIn("da poco", bot.take()[0])
        bot.memory.close()

    async def test_data_safe_without_clean_shutdown(self):
        bot = self.start()
        bot.handle_line(":anna!a@casa.it PRIVMSG #linux :mario++")
        path = bot.memory.path
        # nessuna chiusura: come se il PC si spegnesse di colpo; un'altra connessione legge già il dato
        other = sqlite3.connect(path)
        self.assertEqual(other.execute("SELECT score FROM karma").fetchone()[0], 1)
        other.close()
        bot.memory.close()

    async def test_second_instance_is_refused(self):
        bot = self.start()
        with self.assertRaises(DatabaseLocked) as cm:
            RecordingBot(load_config(self.config_path))
        self.assertIn(str(os.getpid()), str(cm.exception))
        bot.memory.close()
        RecordingBot(load_config(self.config_path)).memory.close()  # chiusa la prima, la seconda parte

    async def test_backup_command_and_rotation(self):
        bot = self.start()
        for i in range(3):
            bot.wallclock.now += 1
            bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!backup")
            await bot.drain()
        out = bot.take()
        self.assertTrue(all(line.startswith("NOTICE boss :Backup del database fatto: bot-") for line in out))
        copies = sorted((Path(self.tmp.name) / "copie").glob("bot-*.db"))
        self.assertEqual(len(copies), 2)  # keep: 2
        con = sqlite3.connect(copies[-1])
        self.assertIn("karma", [r[0] for r in con.execute("SELECT name FROM sqlite_master")])
        con.close()
        bot.memory.close()

    async def test_nightly_backup_caught_up_after_restart(self):
        bot = self.start()
        self.assertEqual(bot.run_due_schedules(datetime(2026, 11, 3, 4, 0, tzinfo=ROME)), ["backup-notte"])
        await bot.drain()
        bot.memory.close()
        bot = self.start()  # riavvio: non la ripete
        self.assertEqual(bot.run_due_schedules(datetime(2026, 11, 3, 4, 5, tzinfo=ROME)), [])
        # PC spento alle 4 del giorno dopo, riacceso alle 9: la recupera, una volta sola
        self.assertEqual(bot.run_due_schedules(datetime(2026, 11, 4, 9, 0, tzinfo=ROME)), ["backup-notte"])
        self.assertEqual(bot.run_due_schedules(datetime(2026, 11, 4, 9, 1, tzinfo=ROME)), [])
        await bot.drain()
        self.assertEqual(len(list((Path(self.tmp.name) / "copie").glob("bot-*.db"))), 2)
        bot.memory.close()

    async def test_youtube_channel_id_remembered(self):
        mem = Memory(":memory:")
        from linuxbot.config import SearchConfig, SearchProvider
        from linuxbot.search import Searcher

        calls = []

        def fetch(url, timeout, raw=False):
            calls.append(url)
            if "/channels" in url:
                return {"items": [{"id": "UC" + "z" * 22}]}
            return {"items": []}

        prov = SearchProvider(name="yt", type="youtube", label="YouTube", channels=["@The-Linux-Nerd"], api_key="k")
        cfg = SearchConfig(enabled=True, providers=[prov])
        await Searcher(cfg, fetch, state=lambda: mem).search("prima")
        await Searcher(cfg, fetch, state=lambda: mem).search("seconda")  # come dopo un riavvio
        self.assertEqual(sum("/channels" in c for c in calls), 1)
        mem.close()

    async def test_startup_log_reports_what_was_found(self):
        bot = self.start()
        bot.handle_line(":anna!a@casa.it PRIVMSG #linux :mario++")
        bot.memory.close()
        with self.assertLogs("linuxbot.memory", "INFO") as logs:
            bot = RecordingBot(load_config(self.config_path))
        self.assertTrue(any("utenti con karma: 1" in line for line in logs.output))
        bot.memory.close()


if __name__ == "__main__":
    unittest.main()
