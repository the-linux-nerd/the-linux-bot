import asyncio
import unittest

from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

CONFIG = """
variables: {rules: "http://r"}
channels:
  - name: "#linux"
    topic: "Benvenuti | regole: {rules}"
    description: "Canale di aiuto su Linux"
  - name: "#lock"
    topic: "Topic fisso"
    topic_lock: true
  - "#libero"
commands:
  canale: {permission: admin, actions: [{channel_check: "{arg1}"}]}
"""


class ChannelSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(CONFIG)
        self.bot.INFO_WAIT = 0.05

    def join(self, channel, topic=None, op=True):
        bot = self.bot
        bot.handle_line(f":Bot!b@h JOIN {channel}")
        if topic is not None:
            bot.handle_line(f":srv 332 Bot {channel} :{topic}")
        bot.handle_line(f":srv 353 Bot = {channel} :{'@' if op else ''}Bot anna")
        bot.handle_line(f":srv 366 Bot {channel} :End of /NAMES list.")

    def topics(self):
        return [line for line in self.bot.take() if line.startswith("TOPIC")]

    async def test_sets_topic_when_different(self):
        self.join("#linux", topic="vecchio topic")
        self.assertIn("TOPIC #linux :Benvenuti | regole: http://r", self.bot.take())

    async def test_no_change_when_same_or_unconfigured(self):
        self.join("#linux", topic="Benvenuti   |  regole: http://r")  # spazi diversi: è uguale
        self.join("#libero", topic="qualsiasi")
        self.assertEqual(self.topics(), [])

    async def test_empty_topic_is_set(self):
        self.join("#linux")  # nessun 332: canale senza topic
        self.assertIn("TOPIC #linux :Benvenuti | regole: http://r", self.bot.take())

    async def test_waits_for_op(self):
        with self.assertLogs("linuxbot.channels", "WARNING"):
            self.join("#linux", topic="vecchio", op=False)
        self.assertEqual(self.topics(), [])
        self.bot.handle_line(":ChanServ!s@services MODE #linux +o Bot")
        self.assertEqual(self.topics(), ["TOPIC #linux :Benvenuti | regole: http://r"])

    async def test_topic_lock(self):
        self.join("#lock", topic="Topic fisso")
        self.bot.take()
        self.bot.handle_line(":anna!a@h TOPIC #lock :ho cambiato io")
        self.assertEqual(self.topics(), ["TOPIC #lock :Topic fisso"])
        self.bot.handle_line(":Bot!b@h TOPIC #lock :Topic fisso")   # la nostra eco: niente
        self.assertEqual(self.topics(), [])

    async def test_no_lock_respects_changes(self):
        self.join("#linux", topic="Benvenuti | regole: http://r")
        self.bot.take()
        self.bot.handle_line(":anna!a@h TOPIC #linux :novità del giorno")
        self.assertEqual(self.topics(), [])

    async def test_description_anope(self):
        self.bot.registered = True
        self.bot.services_kind = "anope"
        self.join("#linux", topic="Benvenuti | regole: http://r")
        self.assertIn("PRIVMSG ChanServ :INFO #linux", self.bot.take())
        for line in ("Information for channel #linux:", "Founder: fabio", "Description: vecchia descrizione",
                     "Registered: Jan 01 2026"):
            self.bot.handle_line(f":ChanServ!s@services NOTICE Bot :{line}")
        await self.bot.drain()
        self.assertEqual(self.bot.take(), ["PRIVMSG ChanServ :SET DESC #linux Canale di aiuto su Linux"])

    async def test_description_already_right(self):
        self.bot.registered = True
        self.join("#linux", topic="x")
        self.bot.take()
        self.bot.handle_line(":ChanServ!s@services NOTICE Bot :Description: Canale di aiuto su Linux")
        await self.bot.drain()
        self.assertEqual(self.bot.take(), [])

    async def test_description_atheme_unsupported(self):
        self.bot.registered = True
        self.bot.services_kind = "atheme"
        self.join("#linux", topic="x")
        self.bot.take()
        self.bot.handle_line(":ChanServ!s@services NOTICE Bot :Information on #linux:")
        with self.assertLogs("linuxbot.channels", "WARNING") as logs:
            await self.bot.drain()
        self.assertIn("Atheme", logs.output[0])
        self.assertEqual(self.bot.take(), [])

    async def test_description_checked_once_per_connection_and_one_at_a_time(self):
        bot = make_bot(CONFIG.replace('    topic: "Topic fisso"', '    topic: "Topic fisso"\n    description: "B"'))
        bot.INFO_WAIT = 0.05
        bot.registered = True
        self.bot = bot
        self.join("#linux", topic="x")
        self.join("#lock", topic="Topic fisso")
        infos = [line for line in bot.take() if "INFO" in line]
        self.assertEqual(infos, ["PRIVMSG ChanServ :INFO #linux"])  # #lock aspetta il suo turno
        await asyncio.sleep(0.1)
        self.assertIn("PRIVMSG ChanServ :INFO #lock", bot.take())
        await bot.drain()
        bot.handle_line(":ChanServ!s@services MODE #linux +o Bot")
        self.assertFalse(any("INFO" in line for line in bot.take()))

    async def test_admin_command_forces_check(self):
        self.bot.registered = True
        self.join("#linux", topic="Benvenuti | regole: http://r")
        await self.bot.drain()
        self.bot.take()
        self.bot.handle_line(":boss!b@admin.host PRIVMSG Bot :!canale #linux")
        await self.bot.drain()
        out = self.bot.take()
        self.assertIn("PRIVMSG ChanServ :INFO #linux", out)
        self.assertIn("NOTICE boss :Controllo di topic e descrizione avviato in #linux.", out)

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        with self.assertRaises(ConfigError):
            parse_config({**base, "channels": [{"name": "#x", "topic": "a" * 400}]})
        with self.assertRaises(ConfigError):
            parse_config({**base, "channels": [{"name": "#x", "titolo": "a"}]})
        cfg = parse_config({**base, "channels": [{"name": "#x", "topic": "riga uno\nriga  due"}]})
        self.assertEqual(cfg.channels[0].topic, "riga uno riga due")


if __name__ == "__main__":
    unittest.main()
