import asyncio
import base64
import unittest

from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

NICKSERV = """
identity: {nick: Bot, alt_nicks: [Bot_]}
auth:
  nickserv: {enabled: true, password: pw, wait: 5}
"""


class ServicesTest(unittest.IsolatedAsyncioTestCase):
    def connect(self, extra="", nick_taken=False):
        bot = make_bot(NICKSERV + extra)
        bot.REGAIN_WAIT = 0.5
        bot.registered = False
        bot._register()
        if nick_taken:
            bot.handle_line(":srv 433 * Bot :Nickname is already in use")
        return bot

    def register(self, bot):
        bot.handle_line(f":srv 001 {bot.nick} :Welcome")
        bot.handle_line(f":srv 376 {bot.nick} :End of MOTD")

    async def until(self, bot, predicate, timeout=6):
        for _ in range(int(timeout / 0.05)):
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return predicate()

    # ------------------------------------------------------------------ SASL automatico

    async def test_sasl_auto_uses_nickserv_password(self):
        bot = self.connect()
        self.assertEqual(bot.take()[0], "CAP LS 302")
        bot.handle_line(":srv CAP * LS :multi-prefix sasl=PLAIN,EXTERNAL")
        bot.handle_line(":srv CAP Bot ACK :sasl")
        bot.handle_line("AUTHENTICATE +")
        bot.handle_line(":srv 900 Bot Bot!b@h Bot :You are now logged in as Bot")
        bot.handle_line(":srv 903 Bot :SASL authentication successful")
        sent = bot.take()
        expected = base64.b64encode(b"Bot\x00Bot\x00pw").decode()
        self.assertIn(f"AUTHENTICATE {expected}", sent)
        self.assertEqual(sent[-1], "CAP END")
        self.register(bot)
        await bot.drain()
        sent = bot.take()
        self.assertFalse(any("IDENTIFY" in line for line in sent))   # già identificato con SASL
        self.assertIn("JOIN #linux", sent)                          # niente attesa di NickServ
        self.assertTrue(bot._logged_in)

    async def test_no_sasl_on_server_falls_back_to_identify(self):
        bot = self.connect()
        bot.handle_line(":srv CAP * LS :multi-prefix away-notify")
        self.assertIn("CAP END", bot.take())
        self.register(bot)
        self.assertIn("PRIVMSG NickServ :IDENTIFY pw", bot.take())

    async def test_sasl_failure_falls_back(self):
        bot = self.connect()
        bot.handle_line(":srv CAP * LS :sasl")
        bot.handle_line(":srv CAP Bot ACK :sasl")
        with self.assertLogs("linuxbot.auth", "WARNING"):
            bot.handle_line(":srv 904 Bot :SASL authentication failed")
        self.register(bot)
        self.assertIn("PRIVMSG NickServ :IDENTIFY pw", bot.take())

    async def test_only_external_mechanism(self):
        bot = self.connect()
        bot.handle_line(":srv CAP * LS :sasl=EXTERNAL")
        self.assertNotIn("CAP REQ :sasl", bot.take())

    async def test_server_without_cap(self):
        bot = self.connect()
        bot.handle_line(":srv 421 * CAP :Unknown command")
        self.assertFalse(bot._sasl_in_progress)

    async def test_sasl_disabled(self):
        bot = self.connect("  sasl: {enabled: false}\n")
        self.assertNotIn("CAP LS 302", bot.take())

    # ------------------------------------------------------------------ nick occupato

    async def test_atheme_regain(self):
        bot = self.connect(nick_taken=True)
        bot.handle_line(":srv CAP * LS :away-notify")
        self.register(bot)
        sent = bot.take()
        self.assertIn("PRIVMSG NickServ :IDENTIFY Bot pw", sent)    # dal nick alternativo: con l'account
        self.assertIn("PRIVMSG NickServ \x01VERSION\x01", sent)
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :\x01VERSION atheme-7.2.12. services.example.org\x01")
        self.assertEqual(bot.services_kind, "atheme")
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :You are now identified for Bot.")
        self.assertTrue(await self.until(bot, lambda: "PRIVMSG NickServ :REGAIN Bot pw" in bot.sent))
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :Bot has been regained.")
        self.assertTrue(await self.until(bot, lambda: "NICK Bot" in bot.sent))
        self.assertNotIn("JOIN #linux", bot.sent)                   # aspetta di avere il nick giusto
        bot.handle_line(":Bot_!b@h NICK :Bot")
        await bot.drain()
        self.assertEqual(bot.nick, "Bot")
        self.assertIn("JOIN #linux", bot.take())

    async def test_anope_recover_changes_nick_itself(self):
        bot = self.connect(nick_taken=True)
        self.register(bot)
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :\x01VERSION Anope-2.0.14 -- services.example.org\x01")
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :Password accepted - you are now recognized.")
        self.assertTrue(await self.until(bot, lambda: "PRIVMSG NickServ :RECOVER Bot pw" in bot.sent))
        bot.handle_line(":Bot_!b@h NICK :Bot")                       # Anope ci cambia il nick da solo
        await bot.drain()
        self.assertEqual(bot.sent.count("NICK Bot"), 1)               # solo quello della registrazione
        self.assertIn("JOIN #linux", bot.sent)

    async def test_unknown_services_try_commands_in_order(self):
        bot = self.connect(nick_taken=True)
        self.register(bot)
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :\x01VERSION MioServizio 1.0\x01")
        bot.handle_line(":srv 900 Bot_ Bot_!b@h Bot :You are now logged in as Bot")
        self.assertTrue(await self.until(bot, lambda: "PRIVMSG NickServ :REGAIN Bot pw" in bot.sent))
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :Unknown command REGAIN. Type /msg NickServ HELP.")
        self.assertTrue(await self.until(bot, lambda: "PRIVMSG NickServ :RECOVER Bot pw" in bot.sent))
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :Invalid command. Use /msg NickServ help.")
        self.assertTrue(await self.until(bot, lambda: "PRIVMSG NickServ :GHOST Bot pw" in bot.sent))
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :Ghost with your nick has been killed.")
        self.assertTrue(await self.until(bot, lambda: "NICK Bot" in bot.sent))
        bot.handle_line(":Bot_!b@h NICK :Bot")
        await bot.drain()
        self.assertEqual(bot.nick, "Bot")

    async def test_gives_up_and_takes_nick_when_freed(self):
        bot = self.connect(nick_taken=True)
        bot.config.auth.nickserv.regain_commands = ["REGAIN {nick} {password}"]
        self.register(bot)
        bot.handle_line(":NickServ!s@services NOTICE Bot_ :\x01VERSION atheme\x01")
        with self.assertLogs("linuxbot.auth", "WARNING") as logs:
            bot.handle_line(":NickServ!s@services NOTICE Bot_ :You are now identified for Bot.")
            await bot.drain()                                          # nessuna risposta: si arrende
        self.assertIn("Non sono riuscito a riprendermi il nick Bot", logs.output[-1])
        self.assertIn("JOIN #linux", bot.sent)                         # entra comunque, come Bot_
        bot.take()
        bot.handle_line(":Bot!x@vecchia.connessione QUIT :Ping timeout")  # il vecchio "Bot" se ne va
        self.assertEqual(bot.take(), ["NICK Bot"])

    async def test_no_regain_when_nick_is_ours(self):
        bot = self.connect()
        bot.handle_line(":srv CAP * LS :away-notify")
        self.register(bot)
        self.assertIn("PRIVMSG NickServ :IDENTIFY pw", bot.take())
        bot.handle_line(":NickServ!s@services NOTICE Bot :You are now identified for Bot.")
        await bot.drain()
        self.assertIsNone(bot._regain_task)

    def test_config(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        cfg = parse_config({**base, "auth": {"nickserv": {"enabled": True, "password": "p", "account": "Acc"}}})
        self.assertEqual((cfg.auth.sasl.mode, cfg.auth.sasl.username, cfg.auth.sasl.password), ("auto", "Acc", "p"))
        self.assertTrue(cfg.auth.sasl.enabled)
        cfg = parse_config(base)
        self.assertFalse(cfg.auth.sasl.enabled)  # niente password: niente SASL
        for bad in ({"sasl": {"enabled": "forse"}}, {"nickserv": {"enabled": True, "password": "p", "services": "x"}},
                    {"sasl": {"enabled": True}}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "auth": bad})


if __name__ == "__main__":
    unittest.main()
