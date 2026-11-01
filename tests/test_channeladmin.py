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
        self.assertIn("modello progetto, permanente): non ci sono", out[1])
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


TEMP = CONFIG.replace("channel_management: {default_template: progetto, max_channels: 2}",
                      "channel_management: {default_template: progetto, event_template: evento, max_channels: 5}\n"
                      "behavior: {timezone: Europe/Rome}") \
    .replace("  privato: {topic: \"riservato\", modes: \"+nts\"}",
             "  privato: {topic: \"riservato\", modes: \"+nts\"}\n"
             "  evento: {duration: 1d, topic: \"{channel} aperto fino al {expires}\"}") \
    .replace("  canali: {permission: op, actions: [channel_list]}",
             "  canali: {permission: op, actions: [channel_list]}\n"
             "  evento: {permission: admin, actions: [{channel_create: {channel: \"{arg1}\", spec: \"{rest}\", temporary: true}}]}\n"
             "  proroga: {permission: admin, actions: [{channel_extend: {channel: \"{arg1}\", when: \"{rest}\"}}]}\n"
             "  crea2: {permission: admin, actions: [{channel_create: {channel: \"{arg1}\", spec: \"{rest}\"}}]}")


class TemporaryChannelsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = TEMP.replace("{db}", os.path.join(self.tmp.name, "bot.db"))
        self.t0 = datetime(2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Europe/Rome")).timestamp()

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        bot = make_bot(self.cfg)
        bot.registered = True
        bot._logged_in = True
        bot.INFO_WAIT = 0.01
        bot.wallclock.now = self.t0
        return bot

    async def admin(self, bot, text):
        bot.handle_line(f":boss!b@admin.host PRIVMSG Bot :{text}")
        await bot.drain()
        return bot.take()

    def joined(self, bot, channel):
        bot.handle_line(f":Bot!b@h JOIN {channel}")
        bot.handle_line(f":srv 353 Bot = {channel} :@Bot anna")
        bot.handle_line(f":srv 366 Bot {channel} :End")
        return bot.take()

    def test_parse_when(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from linuxbot.channeladmin import parse_when
        now = datetime(2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
        at = lambda *a: datetime(*a, tzinfo=ZoneInfo("Europe/Rome")).timestamp()  # noqa: E731
        self.assertEqual(parse_when("3d", now)[0], now.timestamp() + 3 * 86400)
        self.assertEqual(parse_when("per 6h", now)[0], now.timestamp() + 6 * 3600)
        self.assertEqual(parse_when("15/11", now)[0], at(2026, 11, 15, 23, 59))
        self.assertEqual(parse_when("fino al 15/11 18:00", now)[0], at(2026, 11, 15, 18, 0))
        self.assertEqual(parse_when("2026-12-01 09:30", now)[0], at(2026, 12, 1, 9, 30))
        self.assertEqual(parse_when("10/01", now)[0], at(2027, 1, 10, 23, 59))      # l'anno prossimo
        self.assertEqual(parse_when("18:30", now)[0], at(2026, 11, 10, 18, 30))     # oggi
        self.assertEqual(parse_when("11:00", now)[0], at(2026, 11, 11, 11, 0))      # domani
        self.assertEqual(parse_when("perm", now)[0], 0.0)
        for bad in ("boh", "31/02", "2026-01-01", ""):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_when(bad, now)[0])

    async def test_event_lifecycle(self):
        bot = self.start()
        out = await self.admin(bot, "!evento #hackathon 15/11 18:00")
        self.assertTrue(any("È temporaneo: chiude fra 5 giorni e 6 ore (15/11/2026 18:00)" in line for line in out), out)
        out = self.joined(bot, "#hackathon")
        self.assertIn("TOPIC #hackathon :#hackathon aperto fino al 15/11/2026 18:00", out)  # modello evento
        # un'ora e mezza prima: niente; un'ora prima: avviso, una volta sola
        bot.wallclock.now = self.t0 + 5 * 86400 + 4.5 * 3600
        self.assertEqual(bot.check_channel_expiry(), [])
        self.assertEqual(bot.take(), [])
        bot.wallclock.now = self.t0 + 5 * 86400 + 5 * 3600
        bot.check_channel_expiry()
        self.assertEqual(bot.take(), ["PRIVMSG #hackathon :⏰ #hackathon è un canale temporaneo: chiude fra 1 ora "
                                      "(15/11/2026 18:00)."])
        bot.check_channel_expiry()
        self.assertEqual(bot.take(), [])
        # alla scadenza: saluto, DROP, uscita, dimenticato
        bot.wallclock.now = self.t0 + 5 * 86400 + 6 * 3600
        self.assertEqual(bot.check_channel_expiry(), ["#hackathon"])
        out = bot.take()
        self.assertEqual(out[0], "PRIVMSG #hackathon :#hackathon chiude adesso: grazie a tutti per aver partecipato!")
        self.assertIn("PRIVMSG ChanServ :DROP #hackathon", out)
        self.assertIn("PART #hackathon :Canale temporaneo chiuso: evento terminato", out)
        self.assertIsNone(bot._channel_cfg("#hackathon"))
        self.assertEqual(bot.memory.channel_list(), [])
        bot.memory.close()

    async def test_template_default_duration_and_required_expiry(self):
        bot = self.start()
        out = await self.admin(bot, "!evento #live")
        self.assertTrue(any("chiude fra 1 giorno" in line for line in out), out)   # dal modello evento
        out = await self.admin(bot, "!evento #altro progetto")                     # progetto non ha durata
        self.assertIn("indica quando chiude", out[0])
        out = await self.admin(bot, "!evento #x 01/01/2020")
        self.assertIn("è già passato", out[0])
        bot.memory.close()

    async def test_crea_permanent_or_with_expiry(self):
        bot = self.start()
        out = await self.admin(bot, "!crea2 #rust")
        self.assertTrue(any("È permanente" in line for line in out))
        out = await self.admin(bot, "!crea2 #meetup privato 2h")
        self.assertTrue(any("con il modello privato" in line and "chiude fra 2 ore" in line for line in out), out)
        bot.memory.close()

    async def test_extend_and_make_permanent(self):
        bot = self.start()
        await self.admin(bot, "!evento #hackathon 2h")
        self.joined(bot, "#hackathon")
        out = await self.admin(bot, "!proroga #hackathon 1d")
        self.assertIn("ora chiude fra 1 giorno", out[0])
        self.assertIn("TOPIC #hackathon", " ".join(out))       # topic aggiornato con la nuova data
        bot.wallclock.now = self.t0 + 3 * 3600
        self.assertEqual(bot.check_channel_expiry(), [])       # non scade più alle 14
        out = await self.admin(bot, "!proroga #hackathon perm")
        self.assertIn("ora è permanente", out[0])
        bot.wallclock.now = self.t0 + 30 * 86400
        self.assertEqual(bot.check_channel_expiry(), [])
        self.assertIn("non è un canale creato da me", (await self.admin(bot, "!proroga #linux 1d"))[0])
        bot.memory.close()

    async def test_expiry_survives_restart_and_closes_when_back(self):
        bot = self.start()
        await self.admin(bot, "!evento #live 2h")
        bot.memory.close()
        bot = self.start()                     # riavvio: la scadenza c'è ancora
        self.assertIsNotNone(bot._channel_cfg("#live").expires)
        bot.wallclock.now = self.t0 + 10 * 3600  # il PC era spento alla scadenza
        self.joined(bot, "#live")
        self.assertEqual(bot.check_channel_expiry(), ["#live"])
        bot.memory.close()

    async def test_list_shows_temporary(self):
        bot = self.start()
        await self.admin(bot, "!evento #hackathon 3d")
        out = await self.admin(bot, "!canali")
        self.assertIn("temporaneo: chiude fra 3 giorni (13/11/2026 12:00)", out[1])
        bot.memory.close()
