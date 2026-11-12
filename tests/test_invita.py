import os
import unittest

import yaml

from tests.helpers import joined, make_bot

ESEMPIO = os.path.join(os.path.dirname(__file__), "..", "config.example.yaml")


def bot_con_invita():
    """Bot con il solo comando !invita di config.example.yaml."""
    with open(ESEMPIO, encoding="utf-8") as f:
        invita = yaml.safe_load(f)["commands"]["invita"]
    bot = make_bot(yaml.safe_dump({"channels": ["#linux"], "commands": {"invita": invita}}))
    joined(bot)
    return bot


class InvitaTest(unittest.IsolatedAsyncioTestCase):
    async def say(self, bot, who, text):
        bot.handle_line(f":{who}!x@{who}.host PRIVMSG #linux :{text}")
        await bot.drain()
        return bot.take()

    async def test_invito_e_istruzioni_autojoin(self):
        out = await self.say(bot_con_invita(), "op", "!invita mario")
        self.assertEqual(out[0], "INVITE mario #linux")
        privati = [line for line in out if line.startswith("PRIVMSG mario :")]
        self.assertTrue(any("op ti invita in #linux" in line for line in privati))
        self.assertTrue(any("/autojoin add #linux" in line for line in privati))
        self.assertEqual(out[-1], "NOTICE op :Invito mandato a mario.")

    async def test_solo_gli_op(self):
        out = await self.say(bot_con_invita(), "normal", "!invita mario")
        self.assertFalse(any(line.startswith(("INVITE", "PRIVMSG mario")) for line in out))


if __name__ == "__main__":
    unittest.main()
