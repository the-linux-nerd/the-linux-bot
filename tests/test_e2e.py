"""Test end-to-end: il bot vero contro un finto server IRC su localhost."""

import asyncio
import unittest

from linuxbot.bot import Bot
from linuxbot.config import parse_config


class FakeIRCd:
    def __init__(self):
        self.lines: asyncio.Queue[str] = asyncio.Queue()
        self.writer = None
        self.connected = asyncio.Event()

    async def handle(self, reader, writer):
        self.writer = writer
        self.connected.set()
        while line := await reader.readline():
            await self.lines.put(line.decode().rstrip("\r\n"))

    def send(self, line):
        self.writer.write((line + "\r\n").encode())

    async def expect(self, prefix, timeout=3):
        while True:
            line = await asyncio.wait_for(self.lines.get(), timeout)
            if line.startswith(prefix):
                return line


class EndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_session(self):
        ircd = FakeIRCd()
        server = await asyncio.start_server(ircd.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config = parse_config({
            "server": {"host": "127.0.0.1", "port": port},
            "identity": {"nick": "Bot"},
            "auth": {"oper": {"enabled": True, "name": "bot", "password": "pw"}},
            "channels": ["#linux"],
            "admins": ["boss!*@*"],
            "behavior": {"flood_rate": 0.01},
            "greetings": {"enabled": True, "delay": 0, "messages": ["Ciao {nick}!"]},
            "commands": {"ping": "pong {nick}", "quit": {"permission": "admin", "actions": [{"quit": "bye"}]}},
        })
        bot = Bot(config)
        task = asyncio.create_task(bot.run())
        try:
            self.assertEqual(await ircd.expect("NICK"), "NICK Bot")
            await ircd.expect("USER")
            ircd.send(":srv 001 Bot :Welcome")
            ircd.send(":srv 376 Bot :End of MOTD")
            self.assertEqual(await ircd.expect("OPER"), "OPER bot pw")
            self.assertEqual(await ircd.expect("JOIN"), "JOIN #linux")
            ircd.send(":Bot!b@h JOIN #linux")
            ircd.send(":srv 353 Bot = #linux :@Bot")
            ircd.send("PING :srv")
            self.assertEqual(await ircd.expect("PONG"), "PONG srv")
            ircd.send(":anna!a@h JOIN #linux")
            self.assertEqual(await ircd.expect("PRIVMSG"), "PRIVMSG #linux :Ciao anna!")
            ircd.send(":anna!a@h PRIVMSG #linux :!ping")
            self.assertEqual(await ircd.expect("PRIVMSG"), "PRIVMSG #linux :pong anna")
            ircd.send(":boss!b@h PRIVMSG Bot :!quit")
            self.assertEqual(await ircd.expect("QUIT"), "QUIT bye")
            ircd.writer.close()
            await asyncio.wait_for(task, 3)
        finally:
            task.cancel()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
