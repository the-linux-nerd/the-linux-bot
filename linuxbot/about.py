"""Il bot spiega sé stesso: presentazione, funzioni attive, comandi e argomenti (azione ``explain``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .config import AUTO_TOPICS, PERMISSION_WORDS, CommandConfig
from .irc import irc_lower, is_channel
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

# "il comando seen", "i ban", "!tempban?" -> "seen", "ban", "tempban"
_FILLER = re.compile(r"^(?:(?:il|lo|la|i|gli|le|l'|un|una|del|della|dei|delle|di)\s+|comando\s+|funzione\s+)+",
                     re.IGNORECASE)
_SELF = {"", "te", "te stesso", "tu", "chi sei", "cosa sai fare", "cosa fai", "tutto", "bot"}

MOD_ACTIONS = {"ban", "kickban", "unban", "kick", "banlist", "baninfo", "op", "voice", "topic"}


class AboutMixin:
    def _at(self: "Bot", key: str, ctx: dict, **extra: Any) -> str:
        return render(self.config.about.texts[key], {**ctx, **extra})

    def _clean_topic(self: "Bot", text: str) -> str:
        topic = " ".join(text.lower().split()).strip(" ?!.,:;\"'«»")
        topic = _FILLER.sub("", topic)
        for prefix in self.config.behavior.command_prefixes:
            if prefix and topic.startswith(prefix):
                topic = topic[len(prefix):]
        if irc_lower(topic) == irc_lower(self.nick):
            return ""
        return topic.strip()

    def _commands_using(self: "Bot", actions: set[str], ctx: dict | None = None) -> list[CommandConfig]:
        result = []
        for cmd in self.config.commands.values():
            if not cmd.enabled or cmd.hidden:
                continue
            if not any(next(iter(a)) in actions for a in cmd.actions):
                continue
            if ctx is not None and not self.has_permission(ctx.get("mask", ""), cmd.permission, ctx.get("channel", "")):
                continue
            result.append(cmd)
        return result

    def _cmd_list(self: "Bot", cmds: list[CommandConfig], ctx: dict, intro: str = " (oppure ") -> str:
        if not cmds:
            return ""
        prefix = ctx.get("prefix", "")
        return intro + ", ".join(f"{prefix}{c.name} {c.usage}".strip() for c in cmds) + ")"

    def _auto_topics(self: "Bot", ctx: dict) -> dict[str, tuple[list[str], str]]:
        """Argomenti generati dalla configurazione, solo per le funzioni attive."""
        cfg = self.config
        values = dict(ctx)
        active: dict[str, bool] = {}
        active["saluti"] = cfg.greetings.enabled
        values["greet_where"] = f" in {', '.join(cfg.greetings.channels)}" if cfg.greetings.channels else ""
        active["memoria"] = cfg.memory.enabled
        values["memory_cmds"] = self._cmd_list(self._commands_using({"seen", "recent"}), ctx)
        memo_cmds = self._commands_using({"memo_add", "memo_read"})
        memo_resps = [r for r in cfg.responses if any(next(iter(a)) == "memo_add" for a in r.actions)]
        active["messaggi"] = cfg.memory.enabled and bool(memo_cmds or memo_resps)
        values["memo_cmds"] = self._cmd_list(memo_cmds, ctx)
        questions = [r.description for r in cfg.responses if r.description]
        active["domande"] = bool(questions)
        values["questions"] = ", ".join(questions)
        mod_all = self._commands_using(MOD_ACTIONS)
        active["moderazione"] = bool(mod_all)
        values["mod_cmds"] = self._cmd_list(self._commands_using(MOD_ACTIONS, ctx), ctx, ": ")
        return {name: (aliases, render(text, values))
                for name, (aliases, text) in AUTO_TOPICS.items() if active.get(name)}

    def _all_topics(self: "Bot", ctx: dict) -> dict[str, tuple[list[str], list[str]]]:
        topics = {name: (aliases, [text]) for name, (aliases, text) in self._auto_topics(ctx).items()}
        for name, topic in self.config.about.topics.items():  # quelli personalizzati vincono
            topics[name] = (topic["aliases"], [render(t, ctx) for t in topic["text"]])
        return topics

    def _explain_command(self: "Bot", cmd: CommandConfig, ctx: dict) -> list[str]:
        prefix = ctx.get("prefix", "")
        head = f"{prefix}{cmd.name} {cmd.usage}".strip()
        lines = [f"{head} — {cmd.description}" if cmd.description else head]
        lines += [render(d, ctx) for d in cmd.details]
        if cmd.examples:
            lines.append(self._at("examples", ctx, examples=", ".join(render(e, ctx) for e in cmd.examples)))
        if cmd.aliases:
            lines.append(self._at("aliases", ctx, aliases=", ".join(prefix + a for a in cmd.aliases)))
        if cmd.permission != "anyone":
            who = PERMISSION_WORDS[cmd.permission]
            allowed = self.has_permission(ctx.get("mask", ""), cmd.permission, ctx.get("channel", ""))
            lines.append(self._at("who_can" if allowed else "not_for_you", ctx, who=who))
        if not cmd.private:
            lines.append(self._at("private_only", ctx))
        return lines

    def explain_lines(self: "Bot", topic: str, ctx: dict) -> list[str]:
        topic = self._clean_topic(topic)
        topics = self._all_topics(ctx)
        if topic in _SELF:
            lines = [render(t, ctx) for t in self.config.about.intro]
            if self.config.about.auto_features:
                auto = self._auto_topics(ctx)
                custom = self.config.about.topics
                features = [text for name, (_, text) in auto.items() if name not in custom]
                if features:
                    lines.append(self._at("features_header", ctx))
                    lines += [f"- {f}" for f in features]
            lines.append(self._at("commands_hint", ctx))
            if self.config.about.topics:
                lines.append(self._at("topics_hint", ctx, topics=", ".join(self.config.about.topics)))
            return lines
        # 1) argomenti scritti nella configurazione, per nome o alias esatto
        for name, topic_cfg in self.config.about.topics.items():
            if topic == name or topic in topic_cfg["aliases"]:
                return topics[name][1]
        # 2) comandi, eventualmente seguiti dalla funzione con lo stesso nome ("messaggi")
        cmd = self.config.find_command(topic)
        if cmd is not None and (not cmd.hidden or self.is_admin(ctx.get("mask", ""))):
            lines = self._explain_command(cmd, ctx)
            if topic in topics and topic not in self.config.about.topics:
                lines += topics[topic][1]
            return lines
        # 3) funzioni attive, per nome/alias esatto e poi per parola contenuta ("ban a tempo")
        for name, (aliases, text) in topics.items():
            if topic == name or topic in aliases:
                return text
        for name, (aliases, text) in topics.items():
            if any(re.search(rf"\b{re.escape(a)}\b", topic) for a in [name, *aliases]):
                return text
        return [self._at("unknown", ctx, topic=topic, topics=", ".join(topics) or "me stesso")]

    def _action_explain(self: "Bot", value: Any, ctx: dict) -> None:
        opts = value if isinstance(value, dict) else {"topic": value if value is not None else "{args}"}
        topic = self._r(opts.get("topic", "{args}"), ctx)
        lines = self.explain_lines(topic, ctx)
        about = self.config.about
        method = str(opts.get("method", about.method))
        nick, target = ctx.get("nick", ""), ctx["_target"]
        if len(lines) <= about.inline_max_lines or method == "say" or not nick:
            for line in lines:
                self.say(target, line)
            return
        for line in lines:
            (self.notice if method == "notice" else self.say)(nick, line)
        if is_channel(target):
            self.say(target, self._at("redirect", ctx))
