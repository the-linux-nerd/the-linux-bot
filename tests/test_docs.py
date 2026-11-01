"""La documentazione deve restare allineata al codice: ogni opzione accettata è documentata e
ogni opzione documentata esiste davvero."""

import dataclasses
import re
import subprocess
import sys
import unittest
from pathlib import Path

from linuxbot import config as c

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = (ROOT / "docs" / "riferimento.md").read_text(encoding="utf-8")


def fields(cls, skip=()):
    return {f.name for f in dataclasses.fields(cls)} - set(skip)


def expected_options() -> set[str]:
    sections = {
        "server": fields(c.ServerConfig),
        "identity": fields(c.IdentityConfig),
        "auth.sasl": (fields(c.SaslConfig) - {"mode"}) | {"enabled"},
        "auth.nickserv": fields(c.NickServConfig),
        "auth.oper": fields(c.OperConfig),
        "channels[]": fields(c.ChannelConfig, {"managed", "template", "expires"}),  # interni, non opzioni
        "channel_management": fields(c.ChannelManagementConfig),
        "behavior": fields(c.BehaviorConfig) | {"command_prefix"},
        "greetings": fields(c.GreetingsConfig),
        "greetings.rules[]": fields(c.GreetingRule),
        "responses[]": fields(c.ResponseConfig, {"compiled"}) | {"pattern"},
        "commands.<nome>": fields(c.CommandConfig, {"name"}),
        "memory": fields(c.MemoryConfig, {"memos"}),
        "memory.memos": fields(c.MemosConfig),
        "bans": fields(c.BansConfig),
        "self_op": fields(c.SelfOpConfig),
        "about": fields(c.AboutConfig, {"topics"}),
        "search": fields(c.SearchConfig),
        "search.providers[]": fields(c.SearchProvider, {"host"}),
        "karma": fields(c.KarmaConfig),
        "schedules[]": fields(c.ScheduleConfig),
        "logging": fields(c.LoggingConfig, {"file", "alerts", "chatlog", "levels"}),
        "logging.file": fields(c.LogFileConfig),
        "logging.alerts": fields(c.AlertsConfig),
        "logging.chatlog": fields(c.ChatLogConfig),
    }
    options = {f"{section}.{key}" for section, keys in sections.items() for key in keys}
    options |= {"auth.user_modes", "admins", "variables", "events.<evento>", "channel_templates.<nome>",
                "about.topics.<nome>", "logging.levels.<area>"}
    return options


def documented_options() -> set[str]:
    reference = REFERENCE.split("## Azioni")[0]
    return set(re.findall(r"^\| `([a-z_][\w.\[\]<>]*)` \|", reference, re.MULTILINE))


class DocsTest(unittest.TestCase):
    def test_every_option_documented(self):
        missing = expected_options() - documented_options()
        self.assertFalse(missing, f"opzioni non documentate in docs/riferimento.md: {sorted(missing)}")

    def test_no_documented_option_that_does_not_exist(self):
        extra = documented_options() - expected_options()
        self.assertFalse(extra, f"opzioni documentate ma inesistenti: {sorted(extra)}")

    def test_every_action_documented(self):
        actions = REFERENCE.split("## Azioni")[1].split("## Variabili")[0]
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", actions, re.MULTILINE))
        self.assertEqual(set(c.ACTIONS) - documented, set())
        self.assertEqual(documented - set(c.ACTIONS), set())

    def test_every_event_documented(self):
        for event in c.EVENTS:
            self.assertIn(f"`{event}`", REFERENCE)

    def test_generated_texts_up_to_date(self):
        out = subprocess.run([sys.executable, str(ROOT / "contrib" / "gen-docs.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_example_configs_are_valid(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"LINUXBOT_NICKSERV_PASSWORD": "prova"}):
            for name in ("config.example.yaml", "config.minimal.yaml"):
                with self.subTest(name=name):
                    c.load_config(ROOT / name)

    def test_links_between_docs_exist(self):
        for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            text = doc.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(((?!https?://|#)[^)#]+)", text):
                with self.subTest(doc=doc.name, link=target):
                    self.assertTrue((doc.parent / target).exists(), f"{doc.name}: link rotto a {target}")

    def test_anchors_exist(self):
        def anchors(text):
            return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
                    for h in re.findall(r"(?m)^#{1,6} (.+)$", text)}
        docs = {p.name: p.read_text(encoding="utf-8") for p in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]}
        for name, text in docs.items():
            for target, anchor in re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", text):
                file = Path(target).name if target else name
                if file in docs:
                    with self.subTest(doc=name, anchor=anchor):
                        self.assertIn(anchor, anchors(docs[file]), f"{name}: ancora rotta {target}#{anchor}")


if __name__ == "__main__":
    unittest.main()
