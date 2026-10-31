import asyncio
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from linuxbot.config import ConfigError, load_config, parse_config
from linuxbot.logsetup import ROOT, configure_logging
from tests.helpers import BASE, RecordingBot, joined, make_bot


def write_config(tmp, extra):
    data = dict(BASE)
    data.update(yaml.safe_load(extra))
    path = Path(tmp) / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_config(path)


class LoggingTestCase(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger(ROOT)
        for h in list(root.handlers):
            if getattr(h, "_linuxbot", False):
                root.removeHandler(h)
                h.close()
        root.setLevel(logging.NOTSET)
        root.propagate = True
        for name in list(logging.root.manager.loggerDict):
            if name.startswith(ROOT + "."):
                logging.getLogger(name).setLevel(logging.NOTSET)

    def read(self, path):
        for h in logging.getLogger(ROOT).handlers:
            h.flush()
        return Path(path).read_text(encoding="utf-8")


class ConfigureTest(LoggingTestCase):
    def test_file_area_levels_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, """
auth: {oper: {enabled: true, name: bot, password: SuperSegreta}}
logging:
  console: false
  file: {path: logs/bot.log}
  levels: {irc: DEBUG, bans: WARNING}
  redact: ['token=(\\w+)']
""")
            configure_logging(cfg)
            logging.getLogger("linuxbot.irc").debug(">> OPER bot SuperSegreta")
            logging.getLogger("linuxbot.irc").debug(">> PRIVMSG NickServ :IDENTIFY Bot altra")
            logging.getLogger("linuxbot.core").info("la password SuperSegreta e token=abc123")
            logging.getLogger("linuxbot.bans").info("non deve comparire")
            logging.getLogger("linuxbot.commands").debug("nemmeno questo")
            text = self.read(Path(tmp) / "logs" / "bot.log")
            self.assertNotIn("SuperSegreta", text)
            self.assertNotIn("altra", text)
            self.assertNotIn("abc123", text)
            self.assertIn("[irc] >> OPER bot ********", text)
            self.assertIn("token=********", text)
            self.assertNotIn("non deve comparire", text)
            self.assertNotIn("nemmeno questo", text)

    def test_size_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, "logging: {console: false, file: {path: b.log, max_bytes: 2KB, backups: 2}}")
            configure_logging(cfg)
            for i in range(200):
                logging.getLogger("linuxbot.core").info("riga numero %d con un po' di testo", i)
            files = sorted(p.name for p in Path(tmp).glob("b.log*"))
            self.assertEqual(files, ["b.log", "b.log.1", "b.log.2"])
            self.assertTrue(all(os.path.getsize(Path(tmp) / f) <= 2048 for f in files))

    def test_json_format_with_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, "logging: {console: false, format: json, file: j.log}")
            configure_logging(cfg)
            try:
                raise ValueError("boom")
            except ValueError:
                logging.getLogger("linuxbot.commands").exception("azione fallita")
            record = json.loads(self.read(Path(tmp) / "j.log").splitlines()[0])
            self.assertEqual((record["level"], record["area"], record["msg"]), ("ERROR", "commands", "azione fallita"))
            self.assertIn("ValueError: boom", record["exc"])

    def test_reconfigure_replaces_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = write_config(tmp, "logging: {console: false, file: a.log}")
            configure_logging(cfg)
            configure_logging(cfg)
            ours = [h for h in logging.getLogger(ROOT).handlers if getattr(h, "_linuxbot", False)]
            self.assertEqual(len(ours), 1)

    def test_invalid_config(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ({"level": "LOUD"}, {"levels": {"boh": "INFO"}}, {"format": "xml"}, {"redact": ["("]},
                    {"file": {"path": "x", "max_bytes": "tanto"}}, {"alerts": {"enabled": True}}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "logging": bad})


class AlertTest(LoggingTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_alerts_rate_limited(self):
        bot = make_bot("logging: {console: false, alerts: {enabled: true, to: ['#staff', boss], min_interval: 60}}")
        bot._queue = asyncio.Queue()  # "connesso"
        configure_logging(bot.config, bot)
        log = logging.getLogger("linuxbot.op")
        log.warning("solo un avviso")
        log.error("Non ho ottenuto l'op in #linux")
        log.error("Non ho ottenuto l'op in #linux")
        self.assertEqual(bot.take(), ["NOTICE #staff :[ERRORE] op: Non ho ottenuto l'op in #linux",
                                      "NOTICE boss :[ERRORE] op: Non ho ottenuto l'op in #linux"])
        self.assertEqual((bot.stats.errors, bot.stats.warnings), (2, 1))

    async def test_no_alert_when_disconnected(self):
        bot = make_bot("logging: {console: false, alerts: {enabled: true, to: ['#staff']}}")
        configure_logging(bot.config, bot)
        logging.getLogger("linuxbot.core").error("errore offline")
        self.assertEqual(bot.take(), [])


class StatsTest(unittest.IsolatedAsyncioTestCase):
    async def test_stats_report(self):
        bot = make_bot("""
memory: {enabled: true, database: ":memory:", memos: {delay: 0}}
commands:
  ping: pong
  stats: {permission: admin, actions: [stats]}
  lento: {actions: [{say: x}]}
responses: [{name: ciao, match: word, patterns: [ciao], reply: ciao!, cooldown: 0}]
""")
        joined(bot)
        for text in ("!ping", "!ping", "ciao a tutti", "!lento"):
            bot.handle_line(f":user!u@h PRIVMSG #linux :{text}")
        await bot.drain()
        bot.take()
        bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!stats")
        await bot.drain()
        out = "\n".join(bot.take())
        self.assertIn("Attivo da", out)
        self.assertIn("Comandi: 4 (ping 2, lento 1, stats 1)", out)
        self.assertIn("Risposte automatiche: 1 (ciao 1)", out)
        self.assertIn("Database:", out)
        self.assertIn("Anti-flood:", out)
        bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!stats flood")
        await bot.drain()
        self.assertEqual(len(bot.take()), 1)

    async def test_slow_action_warning(self):
        bot = make_bot("logging: {slow_threshold: 0.000001}\ncommands: {x: {actions: [{say: ciao}]}}")
        joined(bot)
        with self.assertLogs("linuxbot.core", "WARNING") as logs:
            bot.handle_line(":user!u@h PRIVMSG #linux :!x")
            await bot.drain()
        self.assertIn("Azione lenta: say !x", logs.output[0])

    async def test_slow_db_warning(self):
        bot = make_bot("memory: {enabled: true, database: ':memory:'}\nlogging: {slow_db_threshold: 0.0000001}")
        with self.assertLogs("linuxbot.memory", "WARNING"):
            bot.memory.recent(3600)
        self.assertGreater(bot.stats.db_time.count, 0)


class ChatLogTest(unittest.IsolatedAsyncioTestCase):
    async def test_chat_log_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = make_bot(f"""
logging: {{chatlog: {{enabled: true, directory: "{tmp}", private: true, retention_days: 10}}}}
commands: {{ping: pong}}
""")
            day = 20000 * 86400.0 + 12 * 3600
            bot.wallclock.now = day
            old = Path(tmp) / "linux" / "2000-01-01.log"
            old.parent.mkdir(parents=True)
            old.write_text("vecchio")
            joined(bot)
            for line in [":anna!a@h JOIN #linux",
                         ":anna!a@h PRIVMSG #linux :!ping",
                         ":anna!a@h PRIVMSG #linux :\x01ACTION saluta\x01",
                         ":anna!a@h NICK anna2",
                         ":op!o@h MODE #linux +v anna2",
                         ":op!o@h TOPIC #linux :nuovo topic",
                         ":anna2!a@h PRIVMSG Bot :ciao in privato",
                         ":anna2!a@h QUIT :bye"]:
                bot.handle_line(line)
            await bot.drain()
            bot.chatlog.close()
            from datetime import datetime
            name = datetime.fromtimestamp(day).strftime("%Y-%m-%d") + ".log"
            text = (Path(tmp) / "linux" / name).read_text()
            lines = [l.split("] ", 1)[1] for l in text.splitlines()]
            self.assertEqual(lines, [
                "--> Bot (b@bot.host) è entrato in #linux",
                "--> anna (a@h) è entrato in #linux",
                "<anna> !ping",
                "<Bot> pong",
                "* anna saluta",
                "--- anna ora si chiama anna2",
                "--- op imposta il modo +v anna2",
                "--- op ha cambiato il topic: nuovo topic",
                "<-- anna2 si è disconnesso (bye)",
            ])
            self.assertIn("<anna2> ciao in privato", (Path(tmp) / "_privati" / name).read_text())
            self.assertFalse(old.exists())  # più vecchio di retention_days


if __name__ == "__main__":
    unittest.main()
