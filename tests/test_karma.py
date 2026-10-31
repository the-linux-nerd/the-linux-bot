import os
import tempfile
import unittest

from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

KARMA = """
memory: {database: ":memory:"}
karma: {enabled: true}
channels: ["#linux", "#altro"]
commands:
  karma: {actions: [{karma_show: "{target}"}]}
  top: {actions: [karma_top]}
  karmareset: {permission: op, actions: [{karma_reset: "{arg1}"}]}
  ban: {permission: op, actions: [{kickban: "{arg1}"}]}
responses:
  - {name: grazie, match: regex, patterns: ['^grazie\\s+(?P<chi>\\S+)'], cooldown: 0,
     actions: [{karma_change: {nick: "{chi}"}}]}
"""

USERS = {"anna": "anna!a@casa.it", "mario": "mario!~m@93-40.casa.it", "luigi": "luigi!l@x.org",
         "op": "op!o@op.host", "boss": "boss!b@admin.host"}


class KarmaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self, cfg=KARMA):
        self.bot = make_bot(cfg)
        self.bot.wallclock.now = 1000 * 86400
        for chan in ("#linux", "#altro"):
            self.bot.handle_line(f":Bot!b@bot.host JOIN {chan}")
            self.bot.handle_line(f":srv 353 Bot = {chan} :@Bot @op anna mario luigi boss")
        for full in USERS.values():
            self.bot.handle_line(f":{full} PRIVMSG #linux :ciao")
        self.bot.take()

    async def say(self, who, text, chan="#linux"):
        self.bot.handle_line(f":{USERS[who]} PRIVMSG {chan} :{text}")
        await self.bot.drain()
        return self.bot.take()

    def score(self, nick, chan="#linux"):
        row = self.bot.memory.karma_get(chan, nick)
        return row.score if row else None

    def later(self, seconds):
        self.bot.wallclock.now += seconds

    async def test_plusplus_and_minusminus(self):
        self.assertEqual(await self.say("anna", "mario++ grazie!"),
                         ["PRIVMSG #linux :mario ha ora 1 punto karma (+1 da anna)"])
        self.assertEqual(await self.say("luigi", "MARIO++"), ["PRIVMSG #linux :mario ha ora 2 punti karma (+1 da luigi)"])
        await self.say("anna", "luigi--")
        self.assertEqual((self.score("mario"), self.score("luigi")), (2, -1))

    async def test_multiple_in_one_message(self):
        await self.say("anna", "mario++ luigi++ grazie a tutti")
        self.assertEqual((self.score("mario"), self.score("luigi")), (1, 1))

    async def test_code_and_absent_nicks_ignored(self):
        self.assertEqual(await self.say("anna", "compilo con g++ e c++, poi i++ nel for"), [])
        self.assertEqual(await self.say("anna", "assente++"), [])
        self.assertEqual(await self.say("anna", "a+++b e x-- e foo++bar"), [])

    async def test_self_and_bot(self):
        self.assertIn("non si dà da soli", (await self.say("anna", "anna++"))[0])
        self.assertIn("io non gioco", (await self.say("anna", "Bot++"))[0])
        self.assertIsNone(self.score("anna"))

    async def test_cooldown_and_daily_limit(self):
        await self.say("anna", "mario++")
        out = await self.say("anna", "mario++")
        self.assertEqual(out, ["NOTICE anna :Hai già dato karma a mario da poco: riprova fra 1 ora."])
        self.later(3601)
        await self.say("anna", "mario++")
        self.assertEqual(self.score("mario"), 2)
        bot = make_bot(KARMA.replace("karma: {enabled: true}", "karma: {enabled: true, cooldown: 0, daily_limit: 2}"))
        bot.handle_line(":Bot!b@h JOIN #linux")
        bot.handle_line(":srv 353 Bot = #linux :Bot anna mario luigi")
        for t in ("mario++", "luigi++", "mario++"):
            bot.handle_line(f":anna!a@h PRIVMSG #linux :{t}")
        self.assertIn("NOTICE anna :Per oggi hai già dato 2 punti karma, riprova domani.", bot.take())

    async def test_grazie_response(self):
        self.assertEqual(await self.say("anna", "grazie mario"), ["PRIVMSG #linux :mario ha ora 1 punto karma (+1 da anna)"])
        self.assertEqual(await self.say("anna", "grazie mille"), [])

    async def test_show_rank_and_top(self):
        await self.say("anna", "mario++")
        await self.say("luigi", "mario++")
        await self.say("anna", "luigi++")
        self.assertEqual(await self.say("anna", "!karma mario"),
                         ["PRIVMSG #linux :mario ha 2 punti karma (+2/-0), 1° su 2 in #linux."])
        self.assertEqual(await self.say("anna", "!karma"), ["PRIVMSG #linux :anna non ha ancora punti karma."])
        self.assertEqual(await self.say("anna", "!top"),
                         ["PRIVMSG #linux :Classifica karma in #linux: 1. mario (2), 2. luigi (1)"])
        self.assertEqual(await self.say("anna", "!karma top"), await self.say("anna", "!top"))

    async def test_per_channel_scope(self):
        await self.say("anna", "mario++")
        self.assertEqual(await self.say("anna", "!karma mario", "#altro"),
                         ["PRIVMSG #altro :mario non ha ancora punti karma."])

    async def test_global_scope(self):
        await self.asyncSetUp(KARMA.replace("karma: {enabled: true}", "karma: {enabled: true, scope: global}"))
        await self.say("anna", "mario++")
        self.assertIn("mario ha 1 punto karma", (await self.say("anna", "!karma mario", "#altro"))[0])

    async def test_reset_command_needs_op(self):
        await self.say("anna", "mario++")
        self.assertIn("non hai i permessi", (await self.say("anna", "!karmareset mario"))[0])
        self.assertEqual(await self.say("op", "!karmareset mario"),
                         ["PRIVMSG #linux :Karma di mario azzerato (era 1)."])
        self.assertEqual(self.score("mario"), 0)

    async def test_ban_resets_karma(self):
        await self.say("anna", "mario++")
        await self.say("luigi", "mario++")
        await self.say("anna", "!ban mario")  # anna non è op: niente
        self.assertEqual(self.score("mario"), 2)
        await self.say("op", "!ban mario")
        self.assertEqual(self.score("mario"), 2)  # il karma si azzera quando il server conferma il ban
        self.bot.handle_line(":Bot!b@bot.host MODE #linux +b *!*@93-40.casa.it")
        self.assertEqual(self.score("mario"), 0)
        self.assertEqual(self.bot.memory.karma_get("#linux", "mario").resets, 1)

    async def test_manual_ban_of_absent_user_resets_by_stored_mask(self):
        await self.say("anna", "mario++")
        self.bot.handle_line(":mario!~m@93-40.casa.it QUIT :bye")
        self.bot.handle_line(":op!o@op.host MODE #linux +b *!*@*.casa.it")
        self.assertEqual(self.score("mario"), 0)
        self.assertEqual(self.score("anna"), None)  # anna non aveva karma

    async def test_nick_ban_and_other_channel_untouched(self):
        await self.say("anna", "mario++")
        await self.say("anna", "mario++", "#altro")
        self.bot.handle_line(":op!o@op.host MODE #linux +b mario!*@*")
        self.assertEqual((self.score("mario"), self.score("mario", "#altro")), (0, 1))

    async def test_immune_not_reset(self):
        await self.say("anna", "boss++")
        self.bot.handle_line(":op!o@op.host MODE #linux +b *!*@admin.host")
        self.assertEqual(self.score("boss"), 1)

    async def test_disabled_channels_and_negative(self):
        await self.asyncSetUp(KARMA.replace("karma: {enabled: true}",
                                            "karma: {enabled: true, channels: ['#linux'], allow_negative: false}"))
        self.assertEqual(await self.say("anna", "mario++", "#altro"), [])
        self.assertEqual(await self.say("anna", "mario--"), [])

    async def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = KARMA + f"\nmemory: {{database: '{os.path.join(tmp, 'k.db')}'}}"
            await self.asyncSetUp(cfg)
            await self.say("anna", "mario++")
            self.bot.memory.close()
            await self.asyncSetUp(cfg)
            self.assertEqual(self.score("mario"), 1)
            self.bot.memory.close()

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ({"scope": "mondo"}, {"texts": {"boh": "x"}}, {"zzz": 1}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "karma": bad})


if __name__ == "__main__":
    unittest.main()
