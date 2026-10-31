import unittest

from linuxbot.config import ConfigError, parse_config
from tests.helpers import joined, make_bot

ABOUT = """
memory: {enabled: true, database: ":memory:", memos: {delay: 0}}
greetings: {enabled: true, channels: ["#linux"], messages: [ciao]}
about:
  intro: ["Sono {botnick}{in_channel}."]
  topics:
    regole: {aliases: [rules], text: ["Niente spam.", "Regolamento: http://r"]}
commands:
  info: {actions: [{explain: "{args}"}]}
  seen: {description: "Ultima volta che ho visto qualcuno", usage: "<nick>", examples: ["!seen mario"],
         details: ["Riga di dettaglio."], aliases: [visto], actions: [{seen: "{arg1}"}]}
  messaggi: {description: "I tuoi messaggi", actions: [memo_read]}
  lascia: {usage: "<nick> <testo>", actions: [{memo_add: {to: "{arg1}", text: "{rest}"}}]}
  kick: {description: "Espelle", permission: op, private: false, actions: [{kick: "{arg1}"}]}
  segreto: {hidden: true, reply: x}
responses:
  - {name: distro, description: "quale distro scegliere", match: word, patterns: [distro], reply: Mint}
  - {name: chi, match: regex, patterns: ['chi sei'], addressed: true, cooldown: 0, actions: [{explain: ""}]}
  - {name: come, match: regex, patterns: ['^come si usa (?P<x>.+?)\\??$'], addressed: true, cooldown: 0,
     actions: [{explain: "{x}"}]}
"""


class AboutTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(ABOUT)
        joined(self.bot)  # @op +voiced normal

    async def say(self, text, who="normal!n@h", target="#linux"):
        self.bot.handle_line(f":{who} PRIVMSG {target} :{text}")
        await self.bot.drain()
        return self.bot.take()

    async def test_self_explanation_goes_private_with_features(self):
        out = await self.say("Bot: chi sei?")
        self.assertEqual(out[0], "PRIVMSG normal :Sono Bot di #linux.")
        text = "\n".join(out)
        self.assertIn("Ecco cosa so fare:", text)
        self.assertIn("Saluto chi entra in #linux.", text)
        self.assertIn("Ricordo chi passa", text)
        self.assertIn("(oppure !seen <nick>)", text)
        self.assertIn("riferire un messaggio", text)
        self.assertIn("quale distro scegliere", text)
        self.assertIn("moderare", text)
        self.assertNotIn(": kick", text)  # i comandi da op non vengono elencati a chi non li può usare
        self.assertIn("Altri argomenti: regole.", text)
        self.assertEqual(out[-1], "PRIVMSG #linux :normal: ti ho spiegato tutto in privato.")

    async def test_mod_commands_listed_for_ops(self):
        out = "\n".join(await self.say("!info moderazione", who="op!o@h"))
        self.assertIn(": !kick", out)

    async def test_command_explanation(self):
        out = await self.say("Bot: come si usa !seen?")
        self.assertEqual(out[:4], ["PRIVMSG normal :!seen <nick> — Ultima volta che ho visto qualcuno",
                                   "PRIVMSG normal :Riga di dettaglio.",
                                   "PRIVMSG normal :Esempi: !seen mario",
                                   "PRIVMSG normal :Puoi scriverlo anche: !visto."])
        out = await self.say("!info visto", target="Bot")
        self.assertEqual(out[0], "PRIVMSG normal :!seen <nick> — Ultima volta che ho visto qualcuno")

    async def test_permission_wording(self):
        out = "\n".join(await self.say("!info kick"))
        self.assertIn("non puoi usarlo", out)
        self.assertIn("Si usa solo nei canali", out)
        out = "\n".join(await self.say("!info kick", who="op!o@h"))
        self.assertIn("Chi può usarlo: gli operatori del canale.", out)

    async def test_short_answers_stay_inline(self):
        self.assertEqual(await self.say("!info regole"),
                         ["PRIVMSG #linux :Niente spam.", "PRIVMSG #linux :Regolamento: http://r"])
        out = await self.say("!info rules")
        self.assertEqual(out[0], "PRIVMSG #linux :Niente spam.")

    async def test_command_plus_feature_with_same_name(self):
        out = await self.say("Bot: come si usa i messaggi?")
        self.assertEqual(out[0], "PRIVMSG #linux :!messaggi — I tuoi messaggi")
        self.assertIn("riferire un messaggio", out[1])

    async def test_fuzzy_topic_and_unknown(self):
        out = await self.say("!info come funzionano i ban a tempo")
        self.assertIn("moderare", out[0])
        out = await self.say("!info astrofisica")
        self.assertEqual(len(out), 1)
        self.assertIn('Non so spiegarti "astrofisica"', out[0])
        self.assertIn("memoria", out[0])

    async def test_hidden_commands_not_explained(self):
        self.assertIn("Non so spiegarti", (await self.say("!info segreto"))[0])
        self.assertIn("segreto", (await self.say("!info segreto", who="boss!b@admin.host"))[0])

    async def test_disabled_features_not_listed(self):
        bot = make_bot("commands: {info: {actions: [explain]}}")
        joined(bot)
        bot.handle_line(":normal!n@h PRIVMSG #linux :!info")
        await bot.drain()
        out = "\n".join(bot.take())
        self.assertNotIn("Ecco cosa so fare", out)
        self.assertNotIn("Ricordo chi passa", out)

    async def test_methods(self):
        bot = make_bot(ABOUT.replace('intro: ["Sono {botnick}{in_channel}."]', 'intro: [uno, due, tre]\n  method: notice'))
        joined(bot)
        bot.handle_line(":normal!n@h PRIVMSG #linux :!info")
        await bot.drain()
        out = bot.take()
        self.assertEqual(out[:3], ["NOTICE normal uno", "NOTICE normal due", "NOTICE normal tre"])

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ({"method": "fax"}, {"topics": {"x": {"aliases": ["y"]}}}, {"texts": {"boh": "x"}}, {"x": 1}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "about": bad})


if __name__ == "__main__":
    unittest.main()
