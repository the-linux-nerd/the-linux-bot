import asyncio
import unittest

from tests.helpers import make_bot

SELF_OP = """
self_op: {enabled: true, delay: 0, retry: 0.01, max_retries: 2}
channels: ["#linux", "#altro"]
"""


class SelfOpTest(unittest.IsolatedAsyncioTestCase):
    async def join(self, bot, names="Bot normal", channel="#linux"):
        bot.handle_line(f":Bot!b@bot.host JOIN {channel}")
        bot.handle_line(f":srv 353 Bot = {channel} :{names}")

    def requests(self, bot):
        return [line for line in bot.take() if "ChanServ" in line or "SAMODE" in line]

    async def test_asks_chanserv_and_stops_when_opped(self):
        bot = make_bot(SELF_OP.replace("retry: 0.01", "retry: 0.05"))
        await self.join(bot)
        await asyncio.sleep(0.01)
        self.assertEqual(self.requests(bot), ["PRIVMSG ChanServ :OP #linux Bot"])
        bot.handle_line(":ChanServ!s@services MODE #linux +o Bot")
        await bot.drain()
        self.assertEqual(self.requests(bot), [])
        self.assertTrue(bot.has_op("#linux"))

    async def test_no_request_if_already_op(self):
        bot = make_bot(SELF_OP)
        await self.join(bot, names="@Bot normal")
        await bot.drain()
        self.assertEqual(self.requests(bot), [])

    async def test_owner_mode_counts_as_op(self):
        bot = make_bot(SELF_OP)
        bot.handle_line(":srv 005 Bot PREFIX=(qaohv)~&@%+ :are supported")
        await self.join(bot, names="~Bot")
        await bot.drain()
        self.assertEqual(self.requests(bot), [])

    async def test_retries_then_gives_up(self):
        bot = make_bot(SELF_OP)
        await self.join(bot)
        with self.assertLogs("linuxbot", "ERROR") as logs:
            await bot.drain()
        self.assertEqual(len(self.requests(bot)), 3)  # 1 + max_retries
        self.assertIn("access list", logs.output[0])

    async def test_rerequest_after_deop(self):
        bot = make_bot(SELF_OP + "\nevents: {self_deop: [{say: {to: '#staff', text: '{nick} mi ha deoppato in {channel}'}}]}")
        await self.join(bot, names="@Bot")
        await bot.drain()
        bot.take()
        bot.handle_line(":cattivo!c@h MODE #linux -o Bot")
        await asyncio.sleep(0)
        bot.handle_line(":ChanServ!s@services MODE #linux +o Bot")
        await bot.drain()
        out = bot.take()
        self.assertIn("PRIVMSG #staff :cattivo mi ha deoppato in #linux", out)
        self.assertEqual([l for l in out if "ChanServ" in l], ["PRIVMSG ChanServ :OP #linux Bot"])

    async def test_samode_and_auto(self):
        bot = make_bot(SELF_OP.replace("enabled: true", "enabled: true, method: auto"))
        bot.is_oper = True
        await self.join(bot)
        await asyncio.sleep(0)
        self.assertEqual(self.requests(bot), ["SAMODE #linux +o Bot"])
        bot.handle_line(":srv MODE #linux +o Bot")
        await bot.drain()

    async def test_only_selected_channels_and_custom_command(self):
        bot = make_bot(SELF_OP.replace("max_retries: 2", "max_retries: 0, channels: ['#altro'], "
                                       "service: Q, command: 'OP {channel}'"))
        await self.join(bot)
        await self.join(bot, channel="#altro")
        await bot.drain()
        self.assertEqual([l for l in bot.take() if l.startswith("PRIVMSG Q")], ["PRIVMSG Q :OP #altro"])


class IdentifyBeforeJoinTest(unittest.IsolatedAsyncioTestCase):
    def start(self, extra=""):
        bot = make_bot("auth: {nickserv: {enabled: true, password: pw, wait: 5" + extra + "}}")
        bot.registered = False
        bot.handle_line(":srv 001 Bot :Welcome")
        bot.handle_line(":srv 376 Bot :End of MOTD")
        return bot

    async def test_joins_after_nickserv_confirms(self):
        bot = self.start()
        await asyncio.sleep(0.01)
        self.assertNotIn("JOIN #linux", bot.sent)
        bot.handle_line(":NickServ!s@services NOTICE Bot :Password accepted - you are now recognized.")
        await bot.drain()
        self.assertIn("JOIN #linux", bot.take())

    async def test_joins_after_numeric_900(self):
        bot = self.start()
        bot.handle_line(":srv 900 Bot Bot!b@h Bot :You are now logged in as Bot")
        await bot.drain()
        self.assertIn("JOIN #linux", bot.take())

    async def test_italian_services(self):
        bot = self.start()
        bot.handle_line(":NickServ!s@services NOTICE Bot :Password accettata, ora sei identificato.")
        await bot.drain()
        self.assertIn("JOIN #linux", bot.take())

    async def test_timeout_joins_anyway(self):
        bot = make_bot("auth: {nickserv: {enabled: true, password: pw, wait: 0.05}}")
        bot.registered = False
        bot.handle_line(":srv 376 Bot :End of MOTD")
        with self.assertLogs("linuxbot", "WARNING"):
            await bot.drain()
        self.assertIn("JOIN #linux", bot.take())

    async def test_wrong_password_joins_anyway(self):
        bot = self.start()
        with self.assertLogs("linuxbot", "ERROR"):
            bot.handle_line(":NickServ!s@services NOTICE Bot :Invalid password for Bot.")
        await bot.drain()
        self.assertIn("JOIN #linux", bot.take())


if __name__ == "__main__":
    unittest.main()
