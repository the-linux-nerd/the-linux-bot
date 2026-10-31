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
        os.environ["LB_TEST_EMPTY"] = ""
        cfg = parse_config({"server": {"host": "h"}, "identity": {"nick": "n"},
                            "variables": {"x": "${LB_TEST_EMPTY:-def}", "y": "${LB_TEST_EMPTY}"}})
        self.assertEqual((cfg.variables["x"], cfg.variables["y"]), ("def", ""))
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


class DataDirTest(unittest.TestCase):
    def test_relative_paths_follow_data_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "etc" / "config.yaml"
            cfg_path.parent.mkdir()
            cfg_path.write_text("server: {host: h}\nidentity: {nick: n}\nmemory: {database: bot.db}\n")
            cfg = load_config(cfg_path)
            self.assertEqual(cfg.database_path, cfg_path.parent / "bot.db")
            cfg.data_dir = Path(tmp) / "var"
            self.assertEqual(cfg.database_path, Path(tmp) / "var" / "bot.db")
            self.assertEqual(cfg.resolve("logs/x.log"), Path(tmp) / "var" / "logs" / "x.log")
            self.assertEqual(cfg.resolve("/assoluto/x"), Path("/assoluto/x"))

    def test_cli_data_dir(self):
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, "-m", "linuxbot", "-c", str(ROOT / "config.example.yaml"),
                                  "--data-dir", tmp, "--check"], capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn(f"Database: {Path(tmp).resolve() / 'linuxbot.db'}", out.stdout)
