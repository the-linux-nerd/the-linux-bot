import asyncio
import base64
import unittest

from tests.helpers import joined, make_bot

COMMANDS = """
commands:
  help: {actions: [help]}
  regole: {aliases: [rules], reply: "Regole di {channel}: {url}", cooldown: 30}
  saluta: {reply: "Ciao {target} da {nick}"}
  man: {min_args: 1, usage: "<cmd>", reply: "man {arg1}"}
  kick: {permission: op, min_args: 1, private: false,
         actions: [{kick: {nick: "{arg1}", reason: "{rest}"}}]}
  voce: {permission: voice, reply: "hai la voce"}
  segreto: {permission: admin, hidden: true, reply: "ok capo"}
  multi: {actions: [{say: uno}, {delay: 0}, {notice: {to: "{arg1}", text: due}}, {me: "balla"}]}
  ban: {permission: op, actions: [{kickban: {nick: "{arg1}", reason: bye}}]}
variables: {url: "http://r"}
"""


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    async def say(self, bot, text, who="user!u@user.host", target="#linux"):
        bot.handle_line(f":{who} PRIVMSG {target} :{text}")
        await bot.drain()
        return bot.take()


class RegistrationTest(BotTestCase):
    async def test_oper_nickserv_and_join(self):
        bot = make_bot("""
auth:
  oper: {enabled: true, name: lb, password: pw}
  nickserv: {enabled: true, password: nspw, wait: 0}
  user_modes: "+B"
events:
  connect: [{raw: "PRIVMSG HostServ :ON"}]
  self_join: [{say: {to: ChanServ, text: "OP {channel} {botnick}"}}]
""")
        bot.registered = False
        bot.handle_line(":srv 001 Bot :Welcome")
        bot.handle_line(":srv 376 Bot :End of MOTD")
        await bot.drain()
        sent = bot.take()
        self.assertIn("OPER lb pw", sent)
        self.assertIn("MODE Bot +B", sent)
        self.assertIn("PRIVMSG NickServ :IDENTIFY nspw", sent)
        self.assertIn("PRIVMSG HostServ :ON", sent)
        self.assertIn("JOIN #linux", sent)
        bot.handle_line(":srv 381 Bot :You are now an IRC operator")
        self.assertTrue(bot.is_oper)
        bot.handle_line(":Bot!b@h JOIN #linux")
        await bot.drain()
        self.assertEqual(bot.take(), ["PRIVMSG ChanServ :OP #linux Bot"])

    async def test_sasl(self):
        bot = make_bot("auth: {sasl: {enabled: true, username: acc, password: pw}}")
        bot.registered = False
        bot._register()
        self.assertEqual(bot.take(), ["CAP LS 302", "NICK Bot", "USER linuxbot 0 * :The Linux Bot"])
        bot.handle_line(":srv CAP * LS :multi-prefix sasl=PLAIN,EXTERNAL")
        self.assertEqual(bot.take(), ["CAP REQ :sasl"])
        bot.handle_line(":srv CAP * ACK :sasl")
        self.assertEqual(bot.take(), ["AUTHENTICATE PLAIN"])
        bot.handle_line("AUTHENTICATE +")
        expected = base64.b64encode(b"acc\0acc\0pw").decode()
        self.assertEqual(bot.take(), [f"AUTHENTICATE {expected}"])
        bot.handle_line(":srv 903 Bot :SASL successful")
        self.assertEqual(bot.take(), ["CAP END"])

    async def test_nick_in_use_and_ping(self):
        bot = make_bot("identity: {nick: Bot, alt_nicks: [Bot_]}")
        bot.registered = False
        bot.handle_line(":srv 433 * Bot :Nickname is already in use")
        bot.handle_line(":srv 433 * Bot_ :Nickname is already in use")
        self.assertEqual(bot.take(), ["NICK Bot_", "NICK Bot1"])
        bot.handle_line("PING :abc def")
        self.assertEqual(bot.take(), ["PONG :abc def"])


class CommandTest(BotTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(COMMANDS)
        joined(self.bot)

    async def test_simple_reply_alias_and_prefix(self):
        self.assertEqual(await self.say(self.bot, "!rules"), ["PRIVMSG #linux :Regole di #linux: http://r"])

    async def test_cooldown(self):
        await self.say(self.bot, "!regole")
        self.assertEqual(await self.say(self.bot, "!regole"), [])
        self.bot.clock.now += 31
        self.assertEqual(len(await self.say(self.bot, "!regole")), 1)

    async def test_admin_bypasses_cooldown(self):
        await self.say(self.bot, "!regole", who="boss!b@admin.host")
        self.assertEqual(len(await self.say(self.bot, "!regole", who="boss!b@admin.host")), 1)

    async def test_target_and_addressed(self):
        self.assertEqual(await self.say(self.bot, "!saluta pippo"), ["PRIVMSG #linux :Ciao pippo da user"])
        self.assertEqual(await self.say(self.bot, "Bot: saluta"), ["PRIVMSG #linux :Ciao user da user"])
        self.assertEqual(await self.say(self.bot, "bot, !saluta x"), ["PRIVMSG #linux :Ciao x da user"])

    async def test_private_query(self):
        self.assertEqual(await self.say(self.bot, "!saluta", target="Bot"), ["PRIVMSG user :Ciao user da user"])

    async def test_min_args(self):
        self.assertEqual(await self.say(self.bot, "!man"), ["NOTICE user :Uso: !man <cmd>"])
        self.assertEqual(await self.say(self.bot, "!man ls"), ["PRIVMSG #linux :man ls"])

    async def test_permissions(self):
        denied = await self.say(self.bot, "!kick normal spam")
        self.assertEqual(denied, ["NOTICE user :user: non hai i permessi per usare !kick."])
        self.assertEqual(await self.say(self.bot, "!kick normal spam e basta", who="op!o@h"),
                         ["KICK #linux normal :spam e basta"])
        self.assertEqual(await self.say(self.bot, "!voce", who="voiced!v@h"), ["PRIVMSG #linux :hai la voce"])
        self.assertEqual(await self.say(self.bot, "!voce", who="op!o@h"), ["PRIVMSG #linux :hai la voce"])
        self.assertEqual(len(await self.say(self.bot, "!voce", who="normal!n@h")), 1)  # notice di rifiuto
        self.assertEqual(await self.say(self.bot, "!segreto", who="boss!x@admin.host"), ["PRIVMSG #linux :ok capo"])

    async def test_kick_not_in_private(self):
        self.assertEqual(await self.say(self.bot, "!kick normal", who="op!o@h", target="Bot"), [])

    async def test_mode_tracking(self):
        self.bot.handle_line(":ChanServ!s@services MODE #linux +o-v normal voiced")
        self.assertEqual(self.bot.user_modes("#linux", "normal"), {"o"})
        self.assertEqual(self.bot.user_modes("#linux", "voiced"), set())
        self.bot.handle_line(":normal!n@h NICK nuovo")
        self.assertEqual(self.bot.user_modes("#linux", "nuovo"), {"o"})

    async def test_multi_actions(self):
        self.assertEqual(await self.say(self.bot, "!multi amico"),
                         ["PRIVMSG #linux uno", "NOTICE amico due", "PRIVMSG #linux :\x01ACTION balla\x01"])

    async def test_kickban_uses_host(self):
        self.bot.handle_line(":spammer!x@bad.host PRIVMSG #linux :spam")
        self.bot.take()
        self.assertEqual(await self.say(self.bot, "!ban spammer", who="op!o@h"),
                         ["MODE #linux +b *!*@bad.host", "KICK #linux spammer bye"])

    async def test_help(self):
        out = await self.say(self.bot, "!help")
        self.assertEqual(len(out), 1)
        self.assertIn("!regole", out[0])
        self.assertNotIn("!segreto", out[0])
        self.assertNotIn("!kick", out[0])  # utente normale: niente comandi da op
        out = await self.say(self.bot, "!help man")
        self.assertEqual(out, ["NOTICE user :!man <cmd>"])

    async def test_ignore_and_unknown(self):
        bot = make_bot(COMMANDS + "\nbehavior: {ignore: ['*!*@spam.host'], unknown_command_reply: '{nick}: boh'}")
        self.assertEqual(await self.say(bot, "!regole", who="x!y@spam.host"), [])
        self.assertEqual(await self.say(bot, "!nonesiste"), ["PRIVMSG #linux :user: boh"])

    async def test_ctcp_version(self):
        self.assertEqual(await self.say(self.bot, "\x01VERSION\x01", target="Bot"),
                         ["NOTICE user :\x01VERSION The Linux Bot\x01"])


class ResponseTest(BotTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot("""
responses:
  - {name: saluto, match: word, patterns: [ciao], addressed: true, reply: "Ciao {nick}!", cooldown: 60}
  - {name: distro, match: regex, patterns: ['(?P<cosa>distro|kernel) (\\w+)'], reply: "{cosa}={group2}"}
  - {name: esatto, match: exact, patterns: ["ping"], reply: pong, cooldown: 0}
""")
        joined(self.bot)

    async def test_addressed(self):
        self.assertEqual(await self.say(self.bot, "ciao a tutti"), [])
        self.assertEqual(await self.say(self.bot, "ciao Bot"), ["PRIVMSG #linux :Ciao user!"])
        self.assertEqual(await self.say(self.bot, "ciao Bot"), [])  # cooldown
        self.assertEqual(await self.say(self.bot, "ciao", target="Bot"), ["PRIVMSG user :Ciao user!"])

    async def test_regex_groups(self):
        self.assertEqual(await self.say(self.bot, "che kernel usate? kernel 6.1"), ["PRIVMSG #linux kernel=usate"])

    async def test_exact(self):
        self.assertEqual(await self.say(self.bot, "ping?"), ["PRIVMSG #linux pong"])
        self.assertEqual(await self.say(self.bot, "ping pong"), [])


class GreetingTest(BotTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot("""
greetings:
  enabled: true
  delay: 0
  cooldown: 100
  messages: ["Benvenuto {nick} su {channel}!"]
  rules:
    - {match: "*!*@trusted.host", actions: [{voice: "{nick}"}]}
    - {match: "boss!*@*", messages: ["Ciao capo"], method: notice, actions: [{op: "{nick}"}]}
    - {match: "ChanServ!*@*", skip: true}
""")
        joined(self.bot)

    async def join(self, who):
        self.bot.handle_line(f":{who} JOIN #linux")
        await self.bot.drain()
        return self.bot.take()

    async def test_greet_and_cooldown(self):
        self.assertEqual(await self.join("anna!a@x"), ["PRIVMSG #linux :Benvenuto anna su #linux!"])
        self.bot.handle_line(":anna!a@x PART #linux")
        self.assertEqual(await self.join("anna!a@x"), [])
        self.bot.clock.now += 101
        self.assertEqual(len(await self.join("anna!a@x")), 1)

    async def test_rules(self):
        self.assertEqual(await self.join("tom!t@trusted.host"),
                         ["PRIVMSG #linux :Benvenuto tom su #linux!", "MODE #linux +v tom"])
        self.assertEqual(await self.join("boss!b@admin.host"), ["NOTICE boss :Ciao capo", "MODE #linux +o boss"])
        self.assertEqual(await self.join("ChanServ!s@services"), [])

    async def test_no_greet_if_user_left(self):
        bot = make_bot("greetings: {enabled: true, delay: 0.05, messages: [ciao]}")
        joined(bot)
        bot.handle_line(":fugace!f@h JOIN #linux")
        bot.handle_line(":fugace!f@h PART #linux")
        await bot.drain()
        self.assertEqual(bot.take(), [])

    async def test_self_join_not_greeted(self):
        self.assertEqual(await self.join("Bot!b@h"), [])


class KickReloadTest(BotTestCase):
    async def test_rejoin_on_kick(self):
        bot = make_bot("behavior: {rejoin_delay: 0}\nchannels: [{name: '#linux', key: k}]")
        joined(bot)
        bot.handle_line(":op!o@h KICK #linux Bot :fuori")
        await bot.drain()
        self.assertEqual(bot.take(), ["JOIN #linux k"])
        self.assertNotIn("#linux", bot.channels)


if __name__ == "__main__":
    unittest.main()
