import asyncio
import gzip
import io
import json
import unittest
import urllib.parse
from unittest import mock

from linuxbot import search as search_mod
from linuxbot.config import ConfigError, parse_config
from linuxbot.templates import render
from tests.helpers import joined, make_bot

SEARCH = """
search:
  enabled: true
  max_results: 3
  providers:
    - {name: unix, type: stackexchange, site: unix, label: unix.SE}
    - {name: so, type: stackexchange, site: stackoverflow, label: SO}
    - {name: aw, type: mediawiki, api: "https://wiki.archlinux.org/api.php", label: ArchWiki}
commands:
  cerca: {min_args: 1, actions: [{search: "{args}"}]}
  unix: {actions: [{search: {query: "{args}", providers: [unix]}}]}
responses:
  - {name: q, match: regex, patterns: ['^\\?\\?\\s*(?P<q>.+)$'], cooldown: 0, actions: [{search: "{q}"}]}
"""


def se_item(qid, title, score=5, answers=2, accepted=True):
    item = {"question_id": qid, "title": title, "score": score, "answer_count": answers, "link": "https://x"}
    if accepted:
        item["accepted_answer_id"] = 1
    return item


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.data = {
            "unix": {"items": [se_item(1, "How to exclude a dir with rsync"), se_item(2, "rsync &amp; ssh")],
                     "quota_remaining": 250},
            "stackoverflow": {"items": [se_item(3, "rsync <b>exclude</b>", score=100, accepted=False)],
                              "quota_remaining": 250},
        }
        self.wiki = ["rsync", ["Rsync"], [""], ["https://wiki.archlinux.org/title/Rsync"]]

    def __call__(self, url, timeout):
        self.calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "api.php" in url:
            if "aw" in self.fail:
                raise TimeoutError("timed out")
            return self.wiki
        site = q["site"][0]
        if site in self.fail:
            raise OSError("rete giù")
        return self.data.get(site, {"items": []})


class SearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot(SEARCH)
        self.api = FakeAPI()
        self.bot.searcher.fetch = self.api
        joined(self.bot)

    async def say(self, text):
        self.bot.handle_line(f":anna!a@h PRIVMSG #linux :{text}")
        await self.bot.drain()
        return self.bot.take()

    async def test_round_robin_results_and_formatting(self):
        out = await self.say("!cerca rsync exclude folder")
        self.assertEqual(out, [
            "PRIVMSG #linux :[unix.SE] How to exclude a dir with rsync (✔ 5 voti, 2 risposte) https://unix.stackexchange.com/q/1",
            "PRIVMSG #linux :[SO] rsync exclude (100 voti, 2 risposte) https://stackoverflow.com/q/3",
            "PRIVMSG #linux :[ArchWiki] Rsync https://wiki.archlinux.org/title/Rsync",
        ])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.api.calls[0]).query)
        self.assertEqual((query["q"][0], query["answers"][0]), ("rsync exclude folder", "1"))

    async def test_cache(self):
        await self.say("?? rsync exclude")
        calls = len(self.api.calls)
        await self.say("?? RSYNC exclude")
        self.assertEqual(len(self.api.calls), calls)

    async def test_single_provider(self):
        out = await self.say("!unix rsync")
        self.assertEqual(len(out), 2)
        self.assertTrue(all("unix.SE" in line for line in out))

    async def test_partial_failure_and_total_failure(self):
        self.api.fail = {"stackoverflow"}
        with self.assertLogs("linuxbot.search", "WARNING"):
            out = await self.say("!cerca rsync")
        self.assertIn("(non ho potuto cercare su: SO)", out[-1])
        self.api.fail = {"unix", "stackoverflow", "aw"}
        with self.assertLogs("linuxbot.search", "WARNING"):
            out = await self.say("!cerca qualcosa di nuovo")
        self.assertEqual(out, ["PRIVMSG #linux :anna: la ricerca non è disponibile in questo momento, riprova più tardi."])

    async def test_no_results(self):
        self.api.data = {}
        self.api.wiki = ["x", [], [], []]
        out = await self.say("!cerca zzzzqqq")
        self.assertIn('non ho trovato niente per "zzzzqqq"', out[0])

    async def test_too_short_and_usage(self):
        self.assertIn("qualche parola in più", (await self.say("!unix ab"))[0])
        self.assertIn("cosa devo cercare", (await self.say("!unix"))[0])

    async def test_backoff_and_api_error(self):
        self.api.data["unix"]["backoff"] = 30
        await self.say("!cerca prima")
        calls = len(self.api.calls)
        out = await self.say("!cerca seconda")
        self.assertIn("non ho potuto cercare su: unix.SE, SO", out[-1])
        self.assertEqual([c for c in self.api.calls[calls:] if "site=" in c], [])  # SE in pausa, solo wiki
        self.api.data = {"unix": {"error_id": 502, "error_name": "throttle_violation", "error_message": "too many"}}
        self.bot.searcher.backoff_until.clear()
        with self.assertLogs("linuxbot.search", "WARNING") as logs:
            await self.say("!unix terza")
        self.assertIn("throttle_violation", logs.output[0])

    async def test_low_quota_warning(self):
        self.api.data["unix"]["quota_remaining"] = 5
        with self.assertLogs("linuxbot.search", "WARNING") as logs:
            await self.say("!unix quota")
        self.assertIn("Quota Stack Exchange", logs.output[0])

    def test_http_get_handles_gzip(self):
        payload = gzip.compress(json.dumps({"items": []}).encode())

        class Resp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(search_mod.urllib.request, "urlopen", return_value=Resp(payload)) as m:
            self.assertEqual(search_mod.http_get("https://api.stackexchange.com/x", 3), {"items": []})
        self.assertIn("TheLinuxBot", m.call_args[0][0].headers["User-agent"])

    def test_config_validation(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        for bad in ([{"type": "google"}], [{"type": "stackexchange"}], [{"type": "mediawiki", "api": "ftp://x"}],
                    [{"name": "a", "type": "stackexchange", "site": "unix"}, {"name": "a", "type": "stackexchange", "site": "so"}]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_config({**base, "search": {"providers": bad}})


class TemplateFilterTest(unittest.TestCase):
    def test_filters(self):
        v = {"args": "network manager & co", "a": "Ab C"}
        self.assertEqual(render("{args|url}", v), "network+manager+%26+co")
        self.assertEqual(render("{a|path}", v), "Ab%20C")
        self.assertEqual(render("{a|lower|url}", v), "ab+c")
        self.assertEqual(render("{a|boh}", v), "{a|boh}")


class ArgsPatternTest(unittest.IsolatedAsyncioTestCase):
    async def test_args_pattern(self):
        bot = make_bot("commands: {rfc: {usage: '<numero>', args_pattern: '^\\d+$', reply: 'rfc{arg1}'}}")
        joined(bot)
        bot.handle_line(":a!a@h PRIVMSG #linux :!rfc 1149")
        bot.handle_line(":a!a@h PRIVMSG #linux :!rfc abc")
        await bot.drain()
        self.assertEqual(bot.take(), ["PRIVMSG #linux rfc1149", "NOTICE a :Uso: !rfc <numero>"])


if __name__ == "__main__":
    unittest.main()
