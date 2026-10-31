import yaml

from linuxbot.bot import Bot
from linuxbot.config import parse_config

BASE = {
    "server": {"host": "irc.test"},
    "identity": {"nick": "Bot"},
    "channels": ["#linux"],
    "admins": ["boss!*@admin.host"],
}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordingBot(Bot):
    def __init__(self, config):
        super().__init__(config, clock=FakeClock(), wallclock=FakeClock())
        self.sent = []

    def send(self, line, priority=False):
        self._observe_outgoing(line)
        self.sent.append(line)

    def take(self):
        out, self.sent = self.sent, []
        return out


def make_bot(extra_yaml: str = "", **overrides) -> RecordingBot:
    data = dict(BASE)
    if extra_yaml:
        data.update(yaml.safe_load(extra_yaml))
    data.update(overrides)
    bot = RecordingBot(parse_config(data))
    bot.registered = True
    return bot


def joined(bot, channel="#linux", users="@op +voiced normal"):
    bot.handle_line(f":Bot!b@bot.host JOIN {channel}")
    bot.handle_line(f":srv 353 Bot = {channel} :Bot {users}")
    bot.take()
