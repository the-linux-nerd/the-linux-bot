import os
import unittest
from pathlib import Path

from linuxbot.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent


class ConfigTest(unittest.TestCase):
    def test_example_config_is_valid(self):
        cfg = load_config(ROOT / "config.example.yaml")
        self.assertEqual(cfg.identity.nick, "TheLinuxBot")
        self.assertIsNotNone(cfg.find_command("aiuto"))
        self.assertTrue(cfg.auth.oper.enabled)

    def test_env_expansion(self):
        os.environ["LB_TEST_PW"] = "s3cret"
        cfg = parse_config({"server": {"host": "h"}, "identity": {"nick": "n"},
                            "auth": {"oper": {"enabled": True, "password": "${LB_TEST_PW}"}}})
        self.assertEqual(cfg.auth.oper.password, "s3cret")
        cfg = parse_config({"server": {"host": "h"}, "identity": {"nick": "n"},
                            "variables": {"x": "${LB_NOPE_UNSET:-def}"}})
        self.assertEqual(cfg.variables["x"], "def")
        with self.assertRaises(ConfigError):
            parse_config({"server": {"host": "h"}, "identity": {"nick": "n"}, "variables": {"x": "${LB_NOPE_UNSET}"}})

    def test_errors(self):
        base = {"server": {"host": "h"}, "identity": {"nick": "n"}}
        bad = [
            {},
            {"server": {"host": "h"}},
            {**base, "unknown": 1},
            {**base, "commands": {"x": {"actions": [{"explode": 1}]}}},
            {**base, "commands": {"x": {"reply": "a", "permission": "god"}}},
            {**base, "commands": {"x": {"description": "manca reply"}}},
            {**base, "commands": {"x": {"reply": "a", "aliases": ["y"]}, "y": "b"}},
            {**base, "responses": [{"match": "regex", "pattern": "(", "reply": "x"}]},
            {**base, "auth": {"oper": {"enabled": True}}},
            {**base, "events": {"boh": []}},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                parse_config(data)

    def test_short_forms(self):
        cfg = parse_config({"server": {"host": "h"}, "identity": {"nick": "n"},
                            "behavior": {"command_prefix": "?"},
                            "commands": {"Sito": "http://x", "help": {"actions": ["help"]}}})
        self.assertEqual(cfg.behavior.command_prefixes, ["?"])
        self.assertEqual(cfg.commands["sito"].reply, "http://x")
        self.assertEqual(cfg.commands["help"].actions, [{"help": None}])
