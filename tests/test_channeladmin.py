import os
import tempfile
import unittest

from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

CONFIG = """
memory: {enabled: true, database: "{db}", memos: {delay: 0}}
channels: ["#linux"]
channel_management: {default_template: progetto, max_channels: 2}
channel_templates:
  progetto: {topic: "{channel} | progetto", modes: "+nt", owners: ["*!*@owner.host"]}
  privato: {topic: "riservato", modes: "+nts"}
commands:
  crea: {permission: admin, actions: [{channel_create: {channel: "{arg1}", template: "{arg2}"}}]}
  chiudi: {permission: admin, actions: [{channel_close: {channel: "{arg1}", confirm: "{arg2}"}}]}
  canali: {permission: op, actions: [channel_list]}
"""


class ChannelAdminTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = CONFIG.replace("{db}", os.path.join(self.tmp.name, "bot.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        bot = make_bot(self.cfg)
        bot.registered = True
        bot._logged_in = True
        bot.INFO_WAIT = 0.01
        return bot

    async def admin(self, bot, text, target="Bot"):
        bot.handle_line(f":boss!b@admin.host PRIVMSG {target} :{text}")
        await bot.drain()
        return bot.take()

    def joined(self, bot, channel, names="@Bot"):
        bot.handle_line(f":Bot!b@h JOIN {channel}")
        bot.handle_line(f":srv 353 Bot = {channel} :{names}")
        bot.handle_line(f":srv 366 Bot {channel} :End")
        bot.handle_line(f":srv 324 Bot {channel} +")
        return bot.take()

    async def test_create_register_apply_template(self):
        bot = self.start()
        out = await self.admin(bot, "!crea #rust")
        self.assertIn("JOIN #rust", out)
        self.assertTrue(any("Creo #rust con il modello progetto" in line for line in out))
        out = self.joined(bot, "#rust")   # nuovo canale: il bot è il primo, quindi op
        self.assertIn("PRIVMSG ChanServ :REGISTER #rust", out)
        self.assertIn("TOPIC #rust :#rust | progetto", out)
        self.assertIn("MODE #rust +nt", out)
        bot.handle_line(":ChanServ!s@services NOTICE Bot :#rust is now registered to Bot.")
        self.assertEqual(bot.memory.channel_list()[0]["registered"], 1)
        self.assertTrue(bot.is_immune("x!y@owner.host", "#rust"))  # owner dal modello
        bot.memory.close()

    async def test_remembered_after_restart(self):
        bot = self.start()
        await self.admin(bot, "!crea #segreto privato")
        bot.memory.close()
        bot = self.start()
        self.assertEqual([c.name for c in bot.config.channels], ["#linux", "#segreto"])
        await bot._join_channels()
        self.assertIn("JOIN #segreto", bot.take())
        out = self.joined(bot, "#segreto")
        self.assertIn("TOPIC #segreto riservato", out)
        self.assertNotIn("PRIVMSG ChanServ :REGISTER #segreto", out)  # già fatto al primo giro
        bot.memory.close()

    async def test_refusals(self):
        bot = self.start()
        self.assertIn("è già nel file", (await self.admin(bot, "!crea #linux"))[0])
        self.assertIn("Modello sconosciuto: boh", (await self.admin(bot, "!crea #a boh"))[0])
        self.assertIn("Indica il canale", (await self.admin(bot, "!crea rust"))[0])
        await self.admin(bot, "!crea #uno")
        self.assertIn("è già gestito", (await self.admin(bot, "!crea #uno"))[-1])
        await self.admin(bot, "!crea #due")
        self.assertIn("massimo 2", (await self.admin(bot, "!crea #tre"))[0])
        bot.handle_line(":anna!a@h PRIVMSG Bot :!crea #mio")
        await bot.drain()
        self.assertIn("non hai i permessi", bot.take()[0])
        bot.memory.close()

    async def test_close_needs_confirmation_and_handles_drop_code(self):
        bot = self.start()
        await self.admin(bot, "!crea #rust")
        self.joined(bot, "#rust")
        out = await self.admin(bot, "!chiudi #rust")
        self.assertIn("Sicuro?", out[0])
        self.assertNotIn("PART", " ".join(out))
        out = await self.admin(bot, "!chiudi #rust conferma")
        self.assertIn("PRIVMSG ChanServ :DROP #rust", out)
        self.assertIn("PART #rust :Canale chiuso da boss", out)
        # Atheme chiede di confermare con un codice: il bot lo rimanda da solo
        bot.handle_line(":ChanServ!s@services NOTICE Bot :To avoid accidental use of this command, this operation "
                        "has to be confirmed. Please confirm by replying with /msg ChanServ DROP #rust 4f2a9c")
        self.assertEqual(bot.take(), ["PRIVMSG ChanServ :DROP #rust 4f2a9c"])
        self.assertEqual(bot.memory.channel_list(), [])
        self.assertIsNone(bot._channel_cfg("#rust"))
        bot.memory.close()

    async def test_confirmation_expires(self):
        bot = self.start()
        bot.config.channel_management.confirm_seconds = 0
        await self.admin(bot, "!crea #rust")
        await self.admin(bot, "!chiudi #rust")
        out = await self.admin(bot, "!chiudi #rust conferma")
        self.assertIn("Sicuro?", out[0])
        bot.memory.close()

    async def test_cannot_close_static_channel(self):
        bot = self.start()
        self.assertIn("è nel file di configurazione", (await self.admin(bot, "!chiudi #linux conferma"))[0])
        bot.memory.close()

    async def test_unsolicited_drop_code_ignored(self):
        bot = self.start()
        bot.handle_line(":ChanServ!s@services NOTICE Bot :Please confirm with /msg ChanServ DROP #linux abc")
        self.assertEqual(bot.take(), [])
        bot.memory.close()

    async def test_channel_list(self):
        bot = self.start()
        self.joined(bot, "#linux", "@Bot anna mario")
        await self.admin(bot, "!crea #rust")
        out = await self.admin(bot, "!canali")
        self.assertEqual(out[0], "NOTICE boss :#linux (dal file): 3 utenti, op ✓")
        self.assertTrue(out[1].startswith("NOTICE boss :#rust (creato da boss il "))
        self.assertIn("modello progetto): non ci sono", out[1])
        bot.memory.close()

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ({"channel_templates": {"x": {"modes": "+o a"}}},
                    {"channel_templates": {"x": {"name": "#a"}}},
                    {"channel_management": {"default_template": "manca"}},
                    {"channels": ["linux"]},
                    {"channels": ["#a", "#A"]}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, **bad})


if __name__ == "__main__":
    unittest.main()
