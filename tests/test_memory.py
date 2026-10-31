import unittest

from linuxbot.memory import Memory, time_ago
from tests.helpers import joined, make_bot

DAY = 86400

MEMORY = """
memory:
  enabled: true
  database: ":memory:"
  store_messages: true
  memos: {delay: 0, deliver_on_message: false}
commands:
  seen: {actions: [{seen: "{arg1}"}]}
  passati: {actions: [{recent: "{arg1}"}]}
  lascia: {min_args: 2, actions: [{memo_add: {to: "{arg1}", text: "{rest}"}}]}
  messaggi: {actions: [memo_read]}
responses:
  - name: chi
    match: regex
    patterns: ['chi è passato negli ultimi (?P<giorni>\\d+) giorni']
    cooldown: 0
    actions: [{recent: {days: "{giorni}"}}]
  - name: quando
    match: regex
    patterns: ['quando è passato (?P<chi>[^\\s?]+)']
    cooldown: 0
    actions: [{seen: "{chi}"}]
  - name: per-me
    match: regex
    patterns: ['ci sono messaggi per me']
    cooldown: 0
    actions: [memo_read]
  - name: lascia
    match: regex
    patterns: ['^lascia un messaggio a (?P<chi>[^\\s:,]+)[:,]?\\s+(?P<testo>.+)$']
    addressed: true
    cooldown: 0
    actions: [{memo_add: {to: "{chi}", text: "{testo}"}}]
"""


class TimeAgoTest(unittest.TestCase):
    def test_italian(self):
        self.assertEqual(time_ago(0, 10), "pochi secondi fa")
        self.assertEqual(time_ago(0, 3600), "un'ora fa")
        self.assertEqual(time_ago(0, 3 * DAY), "3 giorni fa")
        self.assertEqual(time_ago(0, 8 * DAY), "una settimana fa")


class StoreTest(unittest.TestCase):
    def test_recent_dedup_and_prune(self):
        now = [1000.0 * DAY]
        mem = Memory(":memory:", clock=lambda: now[0])
        mem.record("Anna", "#a", "join")
        now[0] += DAY
        mem.record("anna", "#b", "message", "ciao")
        now[0] += 60
        mem.record("Bob", "#a", "join")
        self.assertEqual([e.nick for e in mem.recent(2 * DAY)], ["Bob", "anna"])
        self.assertEqual([e.nick for e in mem.recent(2 * DAY, "#b")], ["anna"])
        self.assertEqual(mem.last_seen("ANNA").channel, "#b")
        now[0] += 10 * DAY
        mem.prune(5)
        self.assertIsNone(mem.last_seen("anna"))


class MemoryBotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(MEMORY)
        self.bot.wallclock.now = 1000 * DAY
        joined(self.bot)

    async def line(self, line):
        self.bot.handle_line(line)
        await self.bot.drain()
        return self.bot.take()

    async def say(self, text, who="user!u@user.host", target="#linux"):
        return await self.line(f":{who} PRIVMSG {target} :{text}")

    def later(self, seconds):
        self.bot.wallclock.now += seconds
        self.bot.clock.now += seconds

    async def test_seen(self):
        await self.line(":mario!m@h JOIN #linux")
        await self.say("vado a dormire", who="mario!m@h")
        await self.line(":mario!m@h QUIT :Ping timeout")
        self.later(2 * DAY)
        out = await self.say("!seen mario")
        self.assertEqual(len(out), 1)
        self.assertIn("mario è stato visto l'ultima volta 2 giorni fa", out[0])
        self.assertIn("mentre si disconnetteva (Ping timeout)", out[0])
        out = await self.say("quando è passato Mario per l'ultima volta?")
        self.assertIn("2 giorni fa", out[0])
        self.assertEqual(await self.say("!seen sconosciuto"), ["PRIVMSG #linux :Non ho mai visto sconosciuto."])
        self.assertEqual(await self.say("!seen normal"), ["PRIVMSG #linux :normal è qui adesso, in #linux."])
        self.assertEqual(await self.say("!seen Bot"), ["PRIVMSG #linux :Io sono sempre qui!"])

    async def test_seen_last_message(self):
        await self.say("ciao a tutti", who="anna!a@h")
        await self.line(":anna!a@h PART #linux :bye")
        await self.line(":srv 353 Bot = #linux :Bot")  # no-op
        self.bot.channels["#linux"].users.pop("anna", None)
        out = await self.say("!seen anna")
        self.assertIn("mentre usciva (bye)", out[0])

    async def test_recent(self):
        await self.line(":vecchio!v@h JOIN #linux")
        self.later(10 * DAY)
        await self.line(":anna!a@h JOIN #linux")
        self.later(DAY)
        await self.line(":bob!b@h JOIN #linux")
        self.later(3600)
        out = await self.say("chi è passato negli ultimi 3 giorni?")
        self.assertEqual(len(out), 1)
        self.assertIn("negli ultimi 3 giorni", out[0])
        self.assertIn("(3)", out[0])  # anna, bob e user che ha appena scritto
        self.assertIn("bob (un'ora fa)", out[0])
        self.assertNotIn("vecchio", out[0])
        out = await self.say("!passati 30")
        self.assertIn("vecchio", out[0])
        out = await self.say("!passati")  # default 7 giorni
        self.assertNotIn("vecchio", out[0])

    async def test_recent_ignores_ignored_users(self):
        bot = make_bot(MEMORY + "\nbehavior: {ignore: ['*!*@spam']}")
        bot.handle_line(":spammer!s@spam JOIN #linux")
        bot.handle_line(":user!u@h PRIVMSG #linux :!passati")
        await bot.drain()
        out = bot.take()
        self.assertNotIn("spammer", out[0])

    async def test_memo_delivered_on_join(self):
        out = await self.say("!lascia mario ricordati la riunione di domani")
        self.assertEqual(out, ["PRIVMSG #linux :Ok user, riferirò il messaggio a mario appena lo vedo."])
        self.later(3 * 3600)
        out = await self.line(":Mario!m@h JOIN #linux")
        self.assertEqual(out, ["PRIVMSG Mario :Mario, user ti ha lasciato un messaggio 3 ore fa: "
                               "ricordati la riunione di domani"])
        self.assertEqual(await self.line(":Mario!m@h JOIN #linux"), [])  # già consegnato

    async def test_memo_on_request(self):
        await self.say("Bot: lascia un messaggio a anna: sei una grande")
        out = await self.say("ci sono messaggi per me?", who="anna!a@h")
        self.assertEqual(out, ["PRIVMSG anna :anna, user ti ha lasciato un messaggio pochi secondi fa: sei una grande"])
        self.assertEqual(await self.say("ci sono messaggi per me?", who="anna!a@h"),
                         ["PRIVMSG #linux :Nessun messaggio per te, anna."])
        self.assertEqual(await self.say("!messaggi", who="anna!a@h", target="Bot"),
                         ["PRIVMSG anna :Nessun messaggio per te, anna."])

    async def test_memo_on_message_and_no_double_reply(self):
        bot = make_bot(MEMORY.replace("deliver_on_message: false", "deliver_on_message: true"))
        joined(bot)
        bot.handle_line(":user!u@h PRIVMSG #linux :!lascia anna ciao")
        await bot.drain()
        bot.take()
        bot.handle_line(":anna!a@h PRIVMSG #linux :!messaggi")
        await bot.drain()
        self.assertEqual(bot.take(), ["PRIVMSG anna :anna, user ti ha lasciato un messaggio pochi secondi fa: ciao"])

    async def test_memo_validation(self):
        self.assertIn("a te stesso", (await self.say("!lascia user ciao"))[0])
        self.assertIn("già letto", (await self.say("!lascia Bot ciao"))[0])
        self.assertIn("Uso:", (await self.say("!lascia #canale ciao"))[0])
        self.assertIn("troppo lungo", (await self.say("!lascia anna " + "x" * 400))[0])
        for _ in range(10):
            await self.say("!lascia anna ciao")
        self.assertIn("troppi messaggi", (await self.say("!lascia anna ciao"))[0])

    async def test_memo_expires(self):
        await self.say("!lascia anna vecchia notizia")
        self.later(61 * DAY)
        self.assertEqual(await self.line(":anna!a@h JOIN #linux"), [])

    async def test_nick_change_tracked(self):
        await self.line(":anna!a@h JOIN #linux")
        await self.line(":anna!a@h NICK anna_away")
        await self.line(":anna_away!a@h QUIT :bye")
        out = await self.say("!seen anna")
        self.assertIn("mentre cambiava nick in anna_away", out[0])

    async def test_persistence(self):
        import tempfile, os
        from linuxbot.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "bot.db")
            mem = Memory(path)
            mem.add_memo("a", "b", "ciao")
            mem.close()
            mem = Memory(path)
            self.assertEqual([m.text for m in mem.pending("B")], ["ciao"])
            mem.close()
