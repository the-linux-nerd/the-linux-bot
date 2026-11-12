import asyncio
import time
import unittest
from email.utils import formatdate
from unittest import mock

from linuxbot import news
from linuxbot.config import ConfigError, parse_config
from tests.helpers import make_bot

NOW = time.time()

RSS = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Kernel 7.1 released</title><link>https://example.org/k71</link>
<pubDate>{formatdate(NOW - 600)}</pubDate><description>&lt;p&gt;New &lt;b&gt;kernel&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Old news</title><link>https://example.org/old</link>
<pubDate>{formatdate(NOW - 3 * 86400)}</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>OpenSSH CVE</title><link rel="alternate" href="https://example.org/ssh"/>
<updated>2026-11-12T10:00:00Z</updated><summary>Patch now</summary></entry>
</feed>"""

NEWS = """
news:
  enabled: true
  feeds: ["https://example.org/rss"]
"""


class ParseTest(unittest.TestCase):
    def test_rss(self):
        items = news.parse_feed("\n  " + RSS)
        self.assertEqual([i["title"] for i in items], ["Kernel 7.1 released", "Old news"])
        self.assertEqual(items[0]["link"], "https://example.org/k71")
        self.assertEqual(items[0]["summary"], "New kernel")
        self.assertAlmostEqual(items[0]["ts"], NOW - 600, delta=2)

    def test_atom(self):
        (item,) = news.parse_feed(ATOM)
        self.assertEqual(item["link"], "https://example.org/ssh")
        self.assertEqual(item["summary"], "Patch now")
        self.assertGreater(item["ts"], 0)

    def test_config(self):
        cfg = parse_config({"server": {"host": "x"}, "identity": {"nick": "b"}})
        self.assertFalse(cfg.news.enabled)
        self.assertTrue(cfg.news.feeds)
        with self.assertRaises(ConfigError):
            parse_config({"server": {"host": "x"}, "identity": {"nick": "b"}, "news": {"feed": []}})


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.bot = make_bot(NEWS)
        self.bot.take()
        self.clock = [NOW]
        self.bot.wallclock = lambda: self.clock[0]
        self.calls = []

        def chat(key, model, messages, timeout):
            self.calls.append(messages[1]["content"])
            return {"scelta": 0, "titolo": "Uscito Linux 7.1", "testo": "Nuovo kernel con novità."}

        patches = [mock.patch.object(news, "http_text", lambda url, timeout: RSS),
                   mock.patch.object(news, "openai_chat", chat),
                   mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-prova"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self):
        asyncio.run(self.bot._news_post("#linux", self.bot.config.news.text, {}))
        return self.bot.take()

    def test_posts_and_never_repeats(self):
        lines = self.post()
        self.assertEqual(lines, ["PRIVMSG #linux :📰 Uscito Linux 7.1",
                                 "PRIVMSG #linux :Nuovo kernel con novità.",
                                 "PRIVMSG #linux :🔗 example.org: https://example.org/k71"])
        self.assertNotIn("Old news", self.calls[0])           # più vecchia di max_age_hours
        self.clock[0] += 3600
        self.assertEqual(self.post(), [])                      # l'unica voce recente è già stata raccontata
        self.assertEqual(len(self.calls), 1)

    def test_same_news_for_every_channel(self):
        self.post()
        asyncio.run(self.bot._news_post("#altro", self.bot.config.news.text, {}))
        self.assertIn("PRIVMSG #altro :📰 Uscito Linux 7.1", self.bot.take())
        self.assertEqual(len(self.calls), 1)

    def test_told_today_goes_to_model(self):
        self.post()
        self.clock[0] += 3600
        with mock.patch.object(news, "http_text", lambda url, timeout: ATOM.replace("2026-11-12T10:00:00Z",
                               time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock[0] - 60)))):
            self.post()
        self.assertIn("- Uscito Linux 7.1", self.calls[1])

    def test_without_key_stays_silent(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.post(), [])
        self.assertEqual(self.calls, [])
