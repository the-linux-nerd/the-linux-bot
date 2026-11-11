import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from linuxbot import activity
from linuxbot.__main__ import main
from linuxbot.memory import Memory

from .helpers import joined, make_bot

MEMORY = """
memory:
  enabled: true
  database: ":memory:"
behavior:
  ignore: ["*!*@spam.host"]
commands:
  mappaattivita: {actions: [{activity_map: "{arg1}"}]}
  mappa: {actions: [{activity_map: {all_channels: true, chars: " ox"}}]}
  mappavuota: {actions: [{activity_map: {channel: "#vuoto", days: 3}}]}
"""

# lunedì 2 novembre 2026, ore 21 locali
MONDAY_21 = datetime(2026, 11, 2, 21, 15).timestamp()


class RenderTest(unittest.TestCase):
    def test_scale_levels(self):
        self.assertEqual(activity.level(0, 10, " .:#"), " ")
        self.assertEqual(activity.level(1, 10, " .:#"), ".")
        self.assertEqual(activity.level(10, 10, " .:#"), "#")
        self.assertEqual(activity.level(6, 10, " .:#"), ":")

    def test_render_aligned(self):
        grid = [[0] * 24 for _ in range(7)]
        grid[0][21] = 4
        grid[6][3] = 2
        lines = activity.render_map(grid, "Lun Mar Mer Gio Ven Sab Dom".split(), "ora", " .:#")
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[0].startswith("ora 00    03    06"))
        self.assertEqual(lines[1][4 + 21 * 2:4 + 22 * 2], "##")
        self.assertEqual(lines[7][4 + 3 * 2:4 + 4 * 2], "::")
        self.assertTrue(lines[1].endswith(" 4") and lines[2].endswith(" 0"))


class MemoryActivityTest(unittest.TestCase):
    def test_add_map_and_replace(self):
        now = [MONDAY_21]
        mem = Memory(":memory:", clock=lambda: now[0])
        mem.activity_add("#Linux")
        mem.activity_add("#linux")
        mem.activity_add("#altro", MONDAY_21 - 8 * 86400)
        grid, first = mem.activity_map("#linux")
        self.assertEqual(grid[0][21], 2)
        self.assertEqual(first, "2026-11-02")
        grid, first = mem.activity_map()
        self.assertEqual(sum(map(sum, grid)), 3)
        self.assertEqual(first, "2026-10-25")
        self.assertEqual(sum(map(sum, mem.activity_map(None, 7)[0])), 2)
        # l'import sostituisce i giorni che porta, senza raddoppiare
        for _ in range(2):
            mem.activity_replace("#linux", {("2026-11-02", 9): 5})
        grid, _ = mem.activity_map("#linux")
        self.assertEqual((grid[0][9], grid[0][21]), (5, 0))
        self.assertEqual(mem.summary()["messaggi nella mappa attività"], 6)


class ActivityBotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(MEMORY)
        self.bot.wallclock.now = MONDAY_21
        joined(self.bot)

    async def line(self, line):
        self.bot.handle_line(line)
        await self.bot.drain()
        return self.bot.take()

    async def test_counts_and_draws(self):
        await self.line(":anna!a@host PRIVMSG #linux :ciao")
        await self.line(":bob!b@host PRIVMSG #linux :ciao anna")
        await self.line(":spam!s@spam.host PRIVMSG #linux :compra")   # ignorato
        await self.line(":anna!a@host PRIVMSG Bot :in privato")        # privato
        out = await self.line(":anna!a@host PRIVMSG #linux :!mappaattivita")
        text = [o.split(" :", 1)[1] for o in out]
        self.assertEqual(text[0], "Attività di #linux dal 02/11/2026: 3 messaggi.")
        self.assertEqual(len(text), 10)
        self.assertTrue(text[2].startswith("Lun ") and text[2].endswith(" 3"))
        self.assertIn("Picco: Lun alle 21:00", text[-1])

    async def test_bot_own_messages_not_counted(self):
        self.bot.say("#linux", "parlo io")
        await self.bot.drain()
        self.bot.take()
        out = await self.line(":anna!a@host PRIVMSG #linux :!mappaattivita 7")
        self.assertIn("Attività di #linux negli ultimi 7 giorni: 1 messaggi.", out[0])

    async def test_all_channels_and_chars(self):
        await self.line(":anna!a@host PRIVMSG #linux :ciao")
        out = await self.line(":anna!a@host PRIVMSG #linux :!mappa")
        self.assertIn("tutti i canali", out[0])
        self.assertIn("xx", out[2])

    async def test_empty(self):
        out = await self.line(":anna!a@host PRIVMSG #linux :!mappavuota")
        self.assertIn("Non ho ancora contato messaggi in #vuoto negli ultimi 3 giorni.", out[-1])


class ImportTest(unittest.TestCase):
    def test_import_weechat_and_chatlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "irc.libera.#linux.weechatlog").write_text(
                "2026-11-02 21:03:11\t@anna\tciao\n"
                "2026-11-02 21:05:00\t+bob\tciao anna\n"
                "2026-11-02 21:06:00\t-->\tcarlo (c@h) è entrato\n"
                "2026-11-02 21:07:00\t *\tanna saluta\n"
                "2026-11-02 21:08:00\tBot\tsono il bot\n", encoding="utf-8")
            (tmp / "linux").mkdir()
            (tmp / "linux" / "2026-11-03.log").write_text(
                "[09:00:01] <anna> buongiorno\n[09:10:00] * anna sbadiglia\n", encoding="utf-8")
            cfg = tmp / "config.yaml"
            cfg.write_text("server: {host: irc.test}\nidentity: {nick: Bot}\nchannels: ['#linux']\n"
                           "memory: {enabled: true, database: bot.db}\n", encoding="utf-8")
            args = ["-c", str(cfg), "--import-activity", str(tmp / "irc.libera.#linux.weechatlog"),
                    str(tmp / "linux")]
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 0)  # rifarlo non raddoppia
            mem = Memory(tmp / "bot.db")
            grid, first = mem.activity_map("#linux")
            mem.close()
            self.assertEqual((grid[0][21], grid[1][9], first), (2, 1, "2026-11-02"))

    def test_unknown_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "a.txt").write_text("", encoding="utf-8")
            cfg = tmp / "config.yaml"
            cfg.write_text("server: {host: irc.test}\nidentity: {nick: Bot}\n"
                           "memory: {enabled: true, database: bot.db}\n", encoding="utf-8")
            self.assertEqual(main(["-c", str(cfg), "--import-activity", str(tmp / "a.txt")]), 2)


if __name__ == "__main__":
    unittest.main()
