import unittest
import urllib.parse

from linuxbot.config import ConfigError, parse_config
from tests.helpers import joined, make_bot

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>The Linux Nerd</title>
 <entry>
  <yt:videoId>AAAAAAAAAAA</yt:videoId>
  <title>Installare Arch Linux passo passo</title>
  <author><name>The Linux Nerd</name></author>
  <published>2026-10-20T18:00:00+00:00</published>
  <media:group><media:description>Guida completa: partizioni, btrfs, systemd-boot.</media:description></media:group>
 </entry>
 <entry>
  <yt:videoId>BBBBBBBBBBB</yt:videoId>
  <title>Permessi dei file spiegati semplice</title>
  <author><name>The Linux Nerd</name></author>
  <published>2026-09-01T18:00:00+00:00</published>
  <media:group><media:description>chmod, chown, umask e sticky bit.</media:description></media:group>
 </entry>
 <entry>
  <yt:videoId>CCCCCCCCCCC</yt:videoId>
  <title>Btrfs: snapshot e rollback</title>
  <author><name>The Linux Nerd</name></author>
  <published>2026-08-01T18:00:00+00:00</published>
  <media:group><media:description>Snapshot automatici con snapper.</media:description></media:group>
 </entry>
</feed>"""

CONFIG = """
search:
  enabled: true
  max_results: 3
  providers:
    - name: yt
      type: youtube
      api_key: "{key}"
      channels: {channels}
commands:
  video: {actions: [{search: {query: "{args}", providers: [yt]}}]}
"""


class FakeYouTube:
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, url, timeout, raw=False):
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        if "feeds/videos.xml" in url:
            assert raw
            return FEED
        if self.error:
            return {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
        if parsed.path.endswith("/channels"):
            return {"items": [{"id": "UC" + "x" * 22}]} if q["forHandle"][0] == "@the-linux-nerd" else {"items": []}
        assert q["channelId"][0] == "UC" + "x" * 22 and q["type"][0] == "video"
        return {"items": [
            {"id": {"kind": "youtube#video", "videoId": "VIDEO000001"},
             "snippet": {"title": "Bash: i cicli for &amp; while", "channelTitle": "The Linux Nerd",
                         "publishedAt": "2026-05-04T10:00:00Z"}},
            {"id": {"kind": "youtube#video", "videoId": "VIDEO000002"},
             "snippet": {"title": "Script bash per principianti", "channelTitle": "The Linux Nerd",
                         "publishedAt": "2025-12-01T10:00:00Z"}},
        ]}


class YouTubeTest(unittest.IsolatedAsyncioTestCase):
    def make(self, key="", channels='["@the-linux-nerd"]'):
        bot = make_bot(CONFIG.replace("{key}", key).replace("{channels}", channels))
        bot.searcher.fetch = self.api = FakeYouTube()
        joined(bot)
        return bot

    async def ask(self, bot, text):
        bot.handle_line(f":anna!a@h PRIVMSG #linux :{text}")
        await bot.drain()
        return bot.take()

    async def test_api_search_with_handle(self):
        bot = self.make(key="K")
        out = await self.ask(bot, "!video bash cicli")
        self.assertEqual(out, [
            "PRIVMSG #linux :[YouTube] Bash: i cicli for & while (The Linux Nerd, 04/05/2026) https://youtu.be/VIDEO000001",
            "PRIVMSG #linux :[YouTube] Script bash per principianti (The Linux Nerd, 01/12/2025) https://youtu.be/VIDEO000002",
        ])
        await self.ask(bot, "!video altra ricerca")
        self.assertEqual(sum("/channels?" in c for c in self.api.calls), 1)  # handle risolto una volta sola

    async def test_api_error_reported(self):
        bot = self.make(key="K")
        self.api.error = True
        with self.assertLogs("linuxbot.search", "WARNING") as logs:
            out = await self.ask(bot, "!video bash")
        self.assertIn("quotaExceeded", logs.output[0])
        self.assertIn("non è disponibile", out[0])

    async def test_feed_without_key(self):
        bot = self.make(channels='["UC' + "y" * 22 + '"]')
        out = await self.ask(bot, "!video snapshot btrfs")
        self.assertEqual(out[0], "PRIVMSG #linux :[YouTube] Btrfs: snapshot e rollback (The Linux Nerd, 01/08/2026) "
                                 "https://youtu.be/CCCCCCCCCCC")
        self.assertIn("Installare Arch Linux", out[1])  # "btrfs" compare nella descrizione
        out = await self.ask(bot, "!video permessi chmod")
        self.assertEqual(len(out), 1)
        self.assertIn("BBBBBBBBBBB", out[0])
        out = await self.ask(bot, "!video kubernetes helm")
        self.assertIn("non ho trovato niente", out[0])
        self.assertFalse(any("googleapis" in c for c in self.api.calls))

    async def test_accents_ignored_in_feed(self):
        bot = self.make(channels='["UC' + "y" * 22 + '"]')
        out = await self.ask(bot, "!video PERMESSÌ file")
        self.assertIn("BBBBBBBBBBB", out[0])

    async def test_handle_without_key_is_skipped_with_warning(self):
        bot = self.make()
        with self.assertLogs("linuxbot.search", "WARNING") as logs:
            out = await self.ask(bot, "!video bash")
        self.assertIn("serve l'ID del canale", logs.output[0])
        self.assertIn("non ho trovato niente", out[0])
        self.assertEqual(self.api.calls, [])

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ([{"type": "youtube"}], [{"type": "youtube", "channels": ["the linux nerd"]}],
                    [{"type": "youtube", "channels": ["@ok_ok"], "order": "random"}]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "search": {"providers": bad}})
        cfg = parse_config({**base, "search": {"providers": [{"name": "y", "type": "youtube", "channels": ["@abc"]}]}})
        self.assertEqual(cfg.search.providers[0].label, "YouTube")


if __name__ == "__main__":
    unittest.main()
