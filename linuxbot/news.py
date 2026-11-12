"""Notizie dai feed RSS/Atom (sezione ``news``, azione ``news``).

Il bot legge i feed configurati, passa le voci recenti a un modello OpenAI che sceglie la più
importante e la racconta in italiano, poi la scrive nel canale. Le notizie già raccontate nella
giornata non si ripetono (lo stato sta nel database, se c'è). Solo libreria standard: urllib in un
thread, così la connessione IRC non si blocca durante le richieste.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from . import __version__
from .templates import render

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("linuxbot.news")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
REUSE_SECONDS = 600     # la stessa scelta vale per tutti i canali dell'attività
KEEP_DAYS = 7           # i link raccontati si ricordano per una settimana

INSTRUCTIONS = (
    "Sei la redazione di The Linux Nerd, canale italiano su GNU/Linux, sicurezza informatica e hacking. "
    "Ricevi un elenco numerato di notizie dai feed e scegli la più importante per chi usa e amministra "
    "Linux: vulnerabilità gravi e sfruttate, rilasci importanti, fatti che cambiano il lavoro dei "
    "sistemisti. Non scegliere una notizia che racconta lo stesso fatto di una già raccontata oggi, "
    "anche se viene da un'altra fonte. Scrivi in italiano ( anche il titolo ), tono diretto da "
    "terminale, senza enfasi: titolo di al massimo 90 caratteri e testo di 2-4 frasi, al massimo "
    "450 caratteri, con i fatti concreti ( versioni, CVE, cosa fare ). Rispondi solo con JSON: "
    '{"scelta": <numero>, "titolo": "...", "testo": "..."}; se nessuna merita, {"scelta": -1}.'
)


def _text(el: ET.Element | None) -> str:
    raw = "".join(el.itertext()) if el is not None else ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def _when(value: str) -> float:
    value = (value or "").strip()
    if not value:
        return 0.0
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if when.tzinfo is None:  # "-0000" o niente fuso: UTC
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def parse_feed(xml: str) -> list[dict]:
    """Voci di un feed RSS 2.0, RSS 1.0 (RDF) o Atom: titolo, link, data (epoch), sommario."""
    root = ET.fromstring(xml.lstrip())
    items = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: c for c in el}
        link = ""
        for c in el:
            if c.tag.rsplit("}", 1)[-1] == "link":
                href = c.get("href") if c.get("rel", "alternate") == "alternate" else None
                link = href or (c.text or "").strip()
                if link:
                    break
        when = 0.0
        for name in ("pubDate", "published", "updated", "date"):
            if name in fields:
                when = _when(fields[name].text or "")
                if when:
                    break
        summary = next((_text(fields[n]) for n in ("description", "summary", "content") if n in fields), "")
        title = _text(fields.get("title"))
        if title and link:
            items.append({"title": title, "link": link, "ts": when, "summary": summary[:400]})
    return items


def http_text(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"TheLinuxBot/{__version__} (IRC bot)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL della configurazione)
        return resp.read(4 * 1024 * 1024).decode("utf-8", "replace")


def openai_chat(key: str, model: str, messages: list[dict], timeout: float) -> dict:
    body: dict[str, Any] = {"model": model, "messages": messages, "response_format": {"type": "json_object"}}
    if model.startswith(("gpt-5", "o")):
        body["reasoning_effort"] = "low"
    req = urllib.request.Request(OPENAI_URL, json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read())
    usage = data.get("usage", {})
    log.info("Notizie: %s, token %s in / %s out", model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return json.loads(data["choices"][0]["message"]["content"])


class NewsMixin:
    _news_state: dict  # stato se non c'è un database

    # ------------------------------------------------------------------ stato

    def _news_load(self: "Bot") -> dict:
        if self.memory is not None:
            return self.memory.state_get("news", {}) or {}
        return getattr(self, "_news_state", {}) or {}

    def _news_save(self: "Bot", state: dict) -> None:
        if self.memory is not None:
            self.memory.state_set("news", state)
        else:
            self._news_state = state

    def _news_key(self: "Bot") -> str:
        cfg = self.config.news
        if cfg.api_key_file:
            return self.config.resolve(cfg.api_key_file).read_text(encoding="utf-8").strip()
        return os.environ.get("OPENAI_API_KEY", "").strip()

    # ------------------------------------------------------------------ scelta (in un thread)

    def _news_pick(self: "Bot", told_today: list[str], seen: set[str], key: str) -> dict | None:
        cfg = self.config.news
        limit = self.wallclock() - cfg.max_age_hours * 3600
        items = []
        for url in cfg.feeds:
            try:
                entries = parse_feed(http_text(url, cfg.timeout))
            except Exception as exc:  # rete, 403, HTML al posto del feed
                log.warning("Notizie: feed %s saltato: %s", url, exc)
                continue
            items += [{**v, "source": re.sub(r"^www\.", "", (v["link"].split("/") + ["", "", ""])[2])}
                      for v in entries[:15] if v["link"] not in seen and v["ts"] >= limit]
        items.sort(key=lambda v: -v["ts"])
        items = items[: cfg.max_candidates]
        if not items:
            log.info("Notizie: nessuna voce nuova nei feed")
            return None
        listing = "\n".join(f"{i}. [{v['source']}] {v['title']} — {v['summary']}" for i, v in enumerate(items))
        already = "\n".join(f"- {t}" for t in told_today) or "nessuna"
        answer = openai_chat(key, cfg.model, [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": f"Già raccontate oggi:\n{already}\n\nNotizie:\n{listing}"},
        ], cfg.timeout * 4)
        choice = answer.get("scelta", -1)
        if not isinstance(choice, int) or not 0 <= choice < len(items):
            log.info("Notizie: il modello non ha scelto niente")
            return None
        item = items[choice]
        return {"title": str(answer.get("titolo") or item["title"]).strip(),
                "summary": str(answer.get("testo") or "").strip(),
                "link": item["link"], "source": item["source"]}

    async def _news_fetch(self: "Bot") -> dict | None:
        """La notizia da raccontare adesso: la stessa per ``REUSE_SECONDS`` (più canali, una sola scelta)."""
        state = self._news_load()
        now = self.wallclock()
        last = state.get("last") or {}
        if now - last.get("ts", 0) < REUSE_SECONDS:
            return last.get("item")
        key = self._news_key()
        if not key:
            log.warning("Notizie: manca la chiave OpenAI (news.api_key_file o OPENAI_API_KEY)")
            return None
        today = self.now_local().date().isoformat()
        told = [t for t in state.get("told", []) if now - t.get("ts", 0) < KEEP_DAYS * 86400]
        seen = {t["link"] for t in told}
        titles = [t["title"] for t in told if t.get("day") == today]
        item = await asyncio.to_thread(self._news_pick, titles, seen, key)
        if item:
            told.append({"day": today, "ts": now, "link": item["link"], "title": item["title"]})
        self._news_save({"told": told, "last": {"ts": now, "item": item}})
        return item

    # ------------------------------------------------------------------ azione

    def _action_news(self: "Bot", value: Any, ctx: dict) -> None:
        if not self.config.news.enabled:
            log.warning("Azione news: la sezione news non è attiva (news.enabled)")
            return
        text = value.get("text") if isinstance(value, dict) else None
        target = (self._r(value.get("to"), ctx) if isinstance(value, dict) else "") or ctx["_target"]
        self._spawn(self._news_post(target, text or self.config.news.text, ctx))

    async def _news_post(self: "Bot", target: str, template: str, ctx: dict) -> None:
        started = time.perf_counter()
        try:
            item = await self._news_fetch()
        except Exception:
            log.exception("Notizie: errore nella scelta")
            return
        if item and target:
            self.say(target, render(template, {**ctx, **item}))
            log.info("Notizia in %s: %s (%.1f s)", target, item["title"], time.perf_counter() - started)
