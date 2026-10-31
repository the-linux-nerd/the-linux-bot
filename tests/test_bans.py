import unittest

from linuxbot.memory import format_duration, parse_duration
from tests.helpers import joined, make_bot

HOUR, DAY = 3600, 86400

BANS = """
memory: {enabled: true, database: ":memory:", memos: {delay: 0}}
bans: {enabled: true}
commands:
  ban: {permission: op, min_args: 1, actions: [{kickban: {nick: "{arg1}", reason: "{rest}", confirm: true}}]}
  tempban: {permission: op, min_args: 2,
            actions: [{kickban: {nick: "{arg1}", duration: "{arg2}", reason: "{rest2}", confirm: true}}]}
  unban: {permission: op, min_args: 1, actions: [{unban: "{arg1}"}]}
  banlist: {permission: op, actions: [banlist]}
  baninfo: {permission: op, min_args: 1, actions: [{baninfo: "{arg1}"}]}
"""


class DurationTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("1d12h"), 1.5 * DAY)
        self.assertEqual(parse_duration("2g"), 2 * DAY)
        self.assertEqual(parse_duration("90"), 90 * 60)
        self.assertEqual(parse_duration("perm"), 0)
        for bad in ("", "abc", "2x", "h", "-1h"):
            self.assertIsNone(parse_duration(bad), bad)

    def test_format(self):
        self.assertEqual(format_duration(2 * HOUR), "2 ore")
        self.assertEqual(format_duration(DAY + 3 * HOUR + 60), "1 giorno e 3 ore")
        self.assertEqual(format_duration(60), "1 minuto")


class BanTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(BANS)
        self.bot.wallclock.now = 1000 * DAY
        self.bot.handle_line(":Bot!b@bot.host JOIN #linux")
        self.bot.handle_line(":srv 353 Bot = #linux :@Bot @op mario")
        self.bot.handle_line(":srv 368 Bot #linux :End of ban list")
        self.bot.handle_line(":mario!~mario@casa.example PRIVMSG #linux :ciao")
        self.bot.take()

    async def line(self, line):
        self.bot.handle_line(line)
        await self.bot.drain()
        return self.bot.take()

    async def op(self, text, target="#linux"):
        return await self.line(f":op!o@op.host PRIVMSG {target} :{text}")

    def later(self, seconds):
        self.bot.wallclock.now += seconds
        self.bot.clock.now += seconds

    async def test_permanent_kickban(self):
        out = await self.op("!ban mario spam continuo")
        self.assertEqual(out[:2], ["MODE #linux +b *!*@casa.example", "KICK #linux mario :spam continuo"])
        self.assertIn("permanente", out[2])
        ban = self.bot.memory.find_bans("#linux")[0]
        self.assertEqual((ban.nick, ban.setter, ban.reason, ban.expires), ("mario", "op", "spam continuo", None))

    async def test_tempban_expires(self):
        out = await self.op("!tempban mario 2h flood")
        self.assertEqual(out[1], "KICK #linux mario :flood (ban per 2 ore)")
        self.assertIn("scade fra 2 ore", out[2])
        self.later(HOUR)
        self.assertEqual(self.bot.check_expired_bans(), 0)
        self.later(HOUR + 1)
        self.assertEqual(self.bot.check_expired_bans(), 1)
        self.assertEqual(self.bot.take(), ["MODE #linux -b *!*@casa.example"])
        self.assertEqual(self.bot.memory.find_bans("#linux"), [])

    async def test_expiry_waits_for_op(self):
        await self.op("!tempban mario 1m")
        self.bot.handle_line(":ChanServ!s@services MODE #linux -o Bot")
        self.later(120)
        self.assertEqual(self.bot.check_expired_bans(), 0)
        self.bot.handle_line(":ChanServ!s@services MODE #linux +o Bot")
        self.assertEqual(self.bot.check_expired_bans(), 1)

    async def test_bad_duration(self):
        out = await self.op("!tempban mario 90 flood")  # numero senza unità = minuti
        self.assertEqual(out[1], "KICK #linux mario :flood (ban per 1 ora e 30 minuti)")
        out = await self.op("!tempban mario boh")
        self.assertEqual(len(out), 1)
        self.assertIn("Durata non valida: boh", out[0])

    async def test_max_duration(self):
        bot = make_bot(BANS.replace("bans: {enabled: true}", "bans: {enabled: true, max_duration: 1d}"))
        joined(bot, users="@op mario")
        bot.handle_line(":op!o@h PRIVMSG #linux :!ban mario")
        await bot.drain()
        self.assertIn("durata massima", bot.take()[0])

    async def test_unban_by_nick_after_quit(self):
        await self.op("!ban mario")
        await self.line(":mario!~mario@casa.example QUIT :bye")
        out = await self.op("!unban mario")
        self.assertEqual(out, ["MODE #linux -b *!*@casa.example",
                               "NOTICE op :Ban rimosso: *!*@casa.example in #linux."])

    async def test_unban_by_id_from_private(self):
        await self.op("!ban mario")
        out = await self.op("!unban #1", target="Bot")
        self.assertEqual(out[0], "MODE #linux -b *!*@casa.example")

    async def test_unban_manual_ban_matching_host(self):
        await self.line(":op!o@op.host MODE #linux +b *!*@*.example")
        out = await self.op("!unban mario")
        self.assertEqual(out[0], "MODE #linux -b *!*@*.example")

    async def test_unban_unknown(self):
        out = await self.op("!unban nessuno")
        self.assertEqual(out[0], "MODE #linux -b nessuno!*@*")
        self.assertIn("Non ho ban registrati", out[1])

    async def test_kickban_by_mask_kicks_everyone_matching(self):
        await self.line(":luigi!l@pc.casa.example JOIN #linux")
        out = await self.op("!ban *!*@*casa.example")
        self.assertEqual(out[0], "MODE #linux +b *!*@*casa.example")
        self.assertEqual(sorted(out[1:3]), ["KICK #linux luigi :Bannato da op", "KICK #linux mario :Bannato da op"])

    async def test_never_ban_self_or_admin(self):
        self.assertEqual(await self.op("!ban Bot"), [])
        await self.line(":boss!b@admin.host JOIN #linux")
        self.assertEqual(await self.op("!ban boss"), ["NOTICE op :boss è immune a ban e kick in #linux."])
        self.assertEqual(len(await self.op("!ban *!*@*")), 1)
        self.assertEqual(self.bot.memory.find_bans("#linux"), [])

    async def test_enforce_reapplies_removed_ban(self):
        await self.op("!ban mario")
        out = await self.line(":altroop!a@h MODE #linux -b *!*@casa.example")
        self.assertEqual(out, ["MODE #linux +b *!*@casa.example"])

    async def test_manual_ban_tracked_and_removable_by_hand(self):
        await self.line(":op!o@op.host MODE #linux +b troll!*@*")
        ban = self.bot.memory.find_bans("#linux")[0]
        self.assertTrue(ban.manual)
        self.assertEqual(ban.setter, "op")
        self.assertEqual(await self.line(":op!o@op.host MODE #linux -b troll!*@*"), [])
        self.assertEqual(self.bot.memory.find_bans("#linux"), [])

    async def test_banned_user_rejoining_is_kicked(self):
        await self.op("!ban mario cattivo")
        out = await self.line(":mario!~mario@casa.example JOIN #linux")
        self.assertEqual(out, ["MODE #linux +b *!*@casa.example",
                               "KICK #linux mario :Sei bannato da questo canale: cattivo"])

    async def test_sync_on_join_restores_and_imports(self):
        await self.op("!ban mario")
        await self.line(":Bot!b@bot.host PART #linux")
        out = await self.line(":Bot!b@bot.host JOIN #linux")
        self.assertEqual(out, ["MODE #linux +b"])
        await self.line(":srv 353 Bot = #linux :Bot")
        await self.line(":srv 367 Bot #linux altro!*@* op!o@h 1700000000")
        out = await self.line(":srv 368 Bot #linux :End of channel ban list")
        self.assertEqual(out, [])  # non ancora op: aspetta
        out = await self.line(":ChanServ!s@services MODE #linux +o Bot")
        self.assertEqual(out, ["MODE #linux +b *!*@casa.example"])
        masks = sorted(b.mask for b in self.bot.memory.find_bans("#linux"))
        self.assertEqual(masks, ["*!*@casa.example", "altro!*@*"])

    async def test_banlist_and_info(self):
        await self.op("!tempban mario 1d spam")
        await self.line(":op!o@op.host MODE #linux +b troll!*@*")
        out = await self.op("!banlist")
        self.assertEqual(out[0], "NOTICE op :Ban attivi in #linux (2):")
        self.assertIn("#1 *!*@casa.example (mario) da op pochi secondi fa, scade fra 1 giorno: spam", out[1])
        self.assertIn("#2 troll!*@* da op", out[2])
        self.assertIn("permanente", out[2])
        out = await self.op("!baninfo mario")
        self.assertIn("#1 *!*@casa.example in #linux (mario): messo da op", out[0])
        await self.op("!unban mario")
        out = await self.op("!baninfo mario")
        self.assertIn("L'ultimo (#1 *!*@casa.example) è stato tolto da op", out[0])
        self.assertIn("Nessun ban registrato per chiunque", (await self.op("!baninfo chiunque"))[0])
        self.assertIn("In quale canale", (await self.op("!banlist", target="Bot"))[0])
        self.assertIn("Ban attivi in #linux", (await self.op("!banlist #linux", target="Bot"))[0])

    async def test_channel_owners_and_global_immune(self):
        cfg = BANS.replace("bans: {enabled: true}", """
bans: {enabled: true, immune: ['*!*@staff.example']}
channels:
  - {name: "#linux", owners: ["*!*@fondatore.example", "carla!*@*"]}
  - {name: "#altro"}
""")
        bot = make_bot(cfg)
        bot.wallclock.now = 1000 * DAY
        for chan in ("#linux", "#altro"):
            bot.handle_line(f":Bot!b@bot.host JOIN {chan}")
            bot.handle_line(f":srv 353 Bot = {chan} :@Bot @op")
        for who in ("anna!a@fondatore.example", "carla!c@x", "staff!s@staff.example", "mario!m@casa"):
            for chan in ("#linux", "#altro"):
                bot.handle_line(f":{who} JOIN {chan}")
        bot.take()

        async def op(text, chan="#linux"):
            bot.handle_line(f":op!o@op.host PRIVMSG {chan} :{text}")
            await bot.drain()
            return bot.take()

        self.assertEqual(await op("!ban anna"), ["NOTICE op :anna è immune a ban e kick in #linux."])
        self.assertIn("immune", (await op("!tempban carla 1h"))[0])
        self.assertIn("immune", (await op("!ban staff"))[0])
        self.assertIn("immune", (await op("!ban staff", "#altro"))[0])   # immune ovunque
        self.assertIn("immune", (await op("!ban *!*@*.example"))[0])     # maschera larga
        out = await op("!ban anna", "#altro")                             # owner solo di #linux
        self.assertEqual(out[0], "MODE #altro +b *!*@fondatore.example")
        self.assertEqual(await op("!kick carla"), [])
        # un op mette a mano un ban che colpisce un owner: il bot lo toglie
        bot.handle_line(":op!o@op.host MODE #linux +b *!*@fondatore.example")
        self.assertEqual(bot.take(), ["MODE #linux -b *!*@fondatore.example"])
        self.assertEqual(bot.memory.find_bans("#linux"), [])
        # anche nella lista ban del server all'ingresso
        bot.handle_line(":Bot!b@bot.host JOIN #linux")
        bot.handle_line(":srv 353 Bot = #linux :@Bot")
        bot.handle_line(":srv 367 Bot #linux carla!*@* x 1")
        bot.handle_line(":srv 368 Bot #linux :end")
        self.assertEqual(bot.take(), ["MODE #linux +b", "MODE #linux -b carla!*@*"])
        # un owner bannato a mano altrove riesce comunque a entrare senza essere espulso
        bot.memory.add_ban("#linux", "*!*@fondatore.example", setter="x")
        bot.handle_line(":anna!a@fondatore.example JOIN #linux")
        await bot.drain()
        self.assertNotIn("KICK", " ".join(bot.take()))

    async def test_mask_type(self):
        bot = make_bot(BANS.replace("bans: {enabled: true}", "bans: {enabled: true, mask: userhost}"))
        joined(bot, users="@op")
        bot.handle_line(":mario!~mario@casa.example JOIN #linux")
        bot.take()
        bot.handle_line(":op!o@h PRIVMSG #linux :!ban mario")
        await bot.drain()
        self.assertEqual(bot.take()[0], "MODE #linux +b *!*mario@casa.example")

    async def test_ban_after_quit_uses_last_seen_host(self):
        await self.line(":mario!~mario@casa.example QUIT :bye")
        out = await self.op("!ban mario")
        self.assertEqual(out[0], "MODE #linux +b *!*@casa.example")

    async def test_persists_across_restart(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "b.db")
            cfg = BANS.replace('":memory:"', f'"{db}"')
            bot = make_bot(cfg)
            joined(bot, users="@op mario")
            bot.handle_line(":op!o@h PRIVMSG #linux :!ban mario")
            await bot.drain()
            bot.memory.close()
            bot2 = make_bot(cfg)
            self.assertEqual(len(bot2.memory.find_bans("#linux")), 1)
            bot2.memory.close()


if __name__ == "__main__":
    unittest.main()
