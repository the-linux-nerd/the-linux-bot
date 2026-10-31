import unittest

from linuxbot.irc import Message, build_line, irc_lower, mask_match, split_message, strip_formatting


class ParseTest(unittest.TestCase):
    def test_privmsg(self):
        m = Message.parse(":nick!user@host PRIVMSG #chan :ciao a tutti")
        self.assertEqual(m.command, "PRIVMSG")
        self.assertEqual(m.params, ["#chan", "ciao a tutti"])
        self.assertEqual((m.prefix.nick, m.prefix.user, m.prefix.host), ("nick", "user", "host"))

    def test_tags_and_numeric(self):
        m = Message.parse("@time=2026-01-01T00:00:00Z;x=a\\sb :srv 001 Bot :Welcome")
        self.assertEqual(m.tags, {"time": "2026-01-01T00:00:00Z", "x": "a b"})
        self.assertEqual(m.command, "001")
        self.assertEqual(m.params, ["Bot", "Welcome"])

    def test_no_trailing(self):
        m = Message.parse("PING srv.example")
        self.assertEqual(m.params, ["srv.example"])
        self.assertIsNone(m.prefix)


class HelpersTest(unittest.TestCase):
    def test_build_line_trailing_and_injection(self):
        self.assertEqual(build_line("PRIVMSG", "#c", "ciao"), "PRIVMSG #c ciao")
        self.assertEqual(build_line("PRIVMSG", "#c", "due parole"), "PRIVMSG #c :due parole")
        self.assertEqual(build_line("PRIVMSG", "#c", "x\r\nQUIT"), "PRIVMSG #c :x  QUIT")

    def test_mask_match(self):
        self.assertTrue(mask_match("*!*@*.example.org", "Nick!u@host.example.org"))
        self.assertTrue(mask_match("NICK[a]!*@*", "nick{a}!u@h"))
        self.assertFalse(mask_match("*!*@other", "nick!u@host"))
        self.assertEqual(irc_lower("A[]\\~"), "a{}|^")

    def test_split(self):
        parts = split_message("parola " * 100, 50)
        self.assertTrue(all(len(p.encode()) <= 50 for p in parts))
        self.assertEqual(" ".join(parts).split(), ["parola"] * 100)
        self.assertTrue(all(len(p.encode()) <= 10 for p in split_message("è" * 30, 10)))

    def test_strip_formatting(self):
        self.assertEqual(strip_formatting("\x0304,01rosso\x03 \x02bold\x02"), "rosso bold")
