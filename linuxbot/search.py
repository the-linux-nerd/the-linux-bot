"""Ricerca di risposte tecniche: siti Stack Exchange (Stack Overflow, unix.SE, Ask Ubuntu...) e wiki MediaWiki.

Usa solo la libreria standard (urllib in un thread), con cache, timeout e rispetto del
``backoff`` richiesto dall'API di Stack Exchange.
"""

from __future__ import annotations

import asyncio
import gzip
import html
import json
import logging
import re
import time
import urllib.parse
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable

from . import __version__
from .config import SearchConfig, SearchProvider

log = logging.getLogger("linuxbot.search")

SE_API = "https://api.stackexchange.com/2.3"
YT_API = "https://www.googleapis.com/youtube/v3"
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
_ATOM = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
         "media": "http://search.yahoo.com/mrss/"}


@dataclass
class Result:
    provider: str
    label: str
    title: str
    link: str
    score: int | None = None
    answers: int | None = None
    accepted: bool = False
    kind: str = "qa"          # qa | wiki | video
    extra: dict = field(default_factory=dict)


def http_get(url: str, timeout: float, raw: bool = False) -> Any:
    """GET di un URL che risponde JSON (anche compresso gzip, come fa sempre Stack Exchange).

    Con ``raw=True`` restituisce il testo (es. il feed XML di un canale YouTube).
    """
    req = urllib.request.Request(url, headers={"User-Agent": f"TheLinuxBot/{__version__} (IRC bot)",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL costruiti da noi)
        body = resp.read(2 * 1024 * 1024)
        if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
    text = body.decode("utf-8")
    return text if raw else json.loads(text)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", html.unescape(str(text)))
    return " ".join(text.split())


class Searcher:
    def __init__(self, cfg: SearchConfig, fetch: Callable[[str, float], Any] = http_get,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.fetch = fetch
        self.clock = clock
        self.cache: dict[tuple, tuple[float, list[Result]]] = {}
        self.backoff_until: dict[str, float] = {}
        self.quota_remaining: int | None = None
        self.requests = 0
        self.yt_channel_ids: dict[str, str] = {}   # @handle -> UC...
        self._warned: set[str] = set()

    # ------------------------------------------------------------------ provider

    def _stackexchange(self, p: SearchProvider, query: str) -> list[Result]:
        params = {"order": "desc", "sort": "relevance", "q": query, "site": p.site,
                  "pagesize": str(self.cfg.max_results)}
        if self.cfg.only_answered:
            params["answers"] = "1"
        if self.cfg.stackexchange_key:
            params["key"] = self.cfg.stackexchange_key
        data = self.fetch(f"{SE_API}/search/advanced?{urllib.parse.urlencode(params)}", self.cfg.timeout)
        if "error_id" in data:
            raise RuntimeError(f"{data.get('error_name')}: {data.get('error_message')}")
        if "quota_remaining" in data:
            self.quota_remaining = int(data["quota_remaining"])
            if self.quota_remaining < 20:
                log.warning("Quota Stack Exchange quasi esaurita: %d richieste rimaste oggi "
                            "(una chiave gratuita in search.stackexchange_key la porta a 10000)", self.quota_remaining)
        if data.get("backoff"):
            self.backoff_until["stackexchange"] = self.clock() + float(data["backoff"])
        results = []
        for item in data.get("items", []):
            qid = item.get("question_id")
            link = f"https://{p.host}/q/{qid}" if qid and p.host else item.get("link", "")
            results.append(Result(p.name, p.label, _clean(item.get("title", "")), link,
                                  item.get("score"), item.get("answer_count"),
                                  bool(item.get("accepted_answer_id"))))
        return results

    def _mediawiki(self, p: SearchProvider, query: str) -> list[Result]:
        params = {"action": "opensearch", "search": query, "limit": str(self.cfg.max_results),
                  "namespace": "0", "format": "json"}
        data = self.fetch(f"{p.api}?{urllib.parse.urlencode(params)}", self.cfg.timeout)
        # [query, [titoli], [descrizioni], [link]]
        if not isinstance(data, list) or len(data) < 4:
            return []
        return [Result(p.name, p.label, _clean(t), link, kind="wiki") for t, link in zip(data[1], data[3])]

    def _yt_channel_id(self, p: SearchProvider, channel: str) -> str:
        """Risolve un handle (@nome) nell'ID del canale, una volta sola (1 unità di quota)."""
        if channel.startswith("UC"):
            return channel
        if channel not in self.yt_channel_ids:
            params = {"part": "id", "forHandle": channel, "key": p.api_key}
            data = self.fetch(f"{YT_API}/channels?{urllib.parse.urlencode(params)}", self.cfg.timeout)
            self._yt_check(data)
            items = data.get("items") or []
            if not items:
                raise RuntimeError(f"canale YouTube {channel} non trovato")
            self.yt_channel_ids[channel] = items[0]["id"]
            log.info("Canale YouTube %s = %s", channel, self.yt_channel_ids[channel])
        return self.yt_channel_ids[channel]

    @staticmethod
    def _yt_check(data: Any) -> None:
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            reason = next((e.get("reason") for e in err.get("errors", []) if e.get("reason")), "")
            raise RuntimeError(f"YouTube: {reason or err.get('code')}: {err.get('message', '')}".strip())

    @staticmethod
    def _yt_date(value: str) -> str:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""

    def _youtube_api(self, p: SearchProvider, channel: str, query: str) -> list[Result]:
        params = {"part": "snippet", "type": "video", "q": query, "channelId": self._yt_channel_id(p, channel),
                  "maxResults": str(self.cfg.max_results), "order": p.order, "key": p.api_key}
        data = self.fetch(f"{YT_API}/search?{urllib.parse.urlencode(params)}", self.cfg.timeout)
        self._yt_check(data)
        results = []
        for item in data.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            sn = item.get("snippet") or {}
            if vid:
                results.append(Result(p.name, p.label, _clean(sn.get("title", "")), f"https://youtu.be/{vid}",
                                      kind="video", extra={"channel": _clean(sn.get("channelTitle", "")),
                                                           "date": self._yt_date(sn.get("publishedAt", ""))}))
        return results

    @staticmethod
    def _words(text: str) -> list[str]:
        text = unicodedata.normalize("NFKD", text.lower())
        text = "".join(c for c in text if not unicodedata.combining(c))
        return re.findall(r"[a-z0-9][a-z0-9+#._-]*", text)

    def _youtube_feed(self, p: SearchProvider, channel: str, query: str) -> list[Result]:
        """Senza chiave: cerca le parole nel feed pubblico del canale (solo gli ultimi 15 video)."""
        xml = self.fetch(YT_FEED + channel, self.cfg.timeout, raw=True)
        root = ET.fromstring(xml)
        wanted = [w for w in self._words(query) if len(w) >= 3] or self._words(query)
        if not wanted:
            return []
        scored = []
        for pos, entry in enumerate(root.findall("a:entry", _ATOM)):
            title = entry.findtext("a:title", "", _ATOM)
            desc = entry.findtext("media:group/media:description", "", _ATOM)
            vid = entry.findtext("yt:videoId", "", _ATOM)
            haystack = set(self._words(f"{title} {desc}"))
            title_words = set(self._words(title))
            hits = sum(1 for w in wanted if w in haystack)
            if not vid or hits * 2 < len(wanted):  # almeno metà delle parole
                continue
            bonus = sum(1 for w in wanted if w in title_words)
            scored.append((hits + bonus, -pos, Result(
                p.name, p.label, _clean(title), f"https://youtu.be/{vid}", kind="video",
                extra={"channel": _clean(entry.findtext("a:author/a:name", "", _ATOM)),
                       "date": self._yt_date(entry.findtext("a:published", "", _ATOM))})))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [r for _, _, r in scored[: self.cfg.max_results]]

    def _youtube(self, p: SearchProvider, query: str) -> list[Result]:
        per_channel = []
        for channel in p.channels:
            if p.api_key:
                per_channel.append(self._youtube_api(p, channel, query))
            elif channel.startswith("UC"):
                per_channel.append(self._youtube_feed(p, channel, query))
            elif channel not in self._warned:
                self._warned.add(channel)
                log.warning("YouTube: per cercare in %s senza api_key serve l'ID del canale (UC...), "
                            "non l'handle; con una api_key va bene anche l'handle. Lo salto.", channel)
        # a turno fra i canali
        merged = []
        for rank in range(self.cfg.max_results):
            merged += [r[rank] for r in per_channel if rank < len(r)]
        return merged[: self.cfg.max_results]

    def _search_one(self, p: SearchProvider, query: str) -> list[Result]:
        if p.type == "stackexchange":
            return self._stackexchange(p, query)
        if p.type == "youtube":
            return self._youtube(p, query)
        return self._mediawiki(p, query)

    # ------------------------------------------------------------------ ricerca

    async def search(self, query: str, providers: list[str] | None = None) -> tuple[list[Result], list[str]]:
        """Cerca su tutti i provider scelti in parallelo. Restituisce (risultati, provider in errore)."""
        chosen = [p for p in self.cfg.providers if not providers or p.name in providers]
        key = (query.lower(), tuple(p.name for p in chosen))
        cached = self.cache.get(key)
        if cached and self.clock() - cached[0] < self.cfg.cache_minutes * 60:
            log.debug("Ricerca '%s' dalla cache", query)
            return cached[1], []

        async def run(p: SearchProvider) -> list[Result] | Exception:
            if p.type == "stackexchange" and self.clock() < self.backoff_until.get("stackexchange", 0):
                return RuntimeError("backoff richiesto dall'API")
            self.requests += 1
            started = time.perf_counter()
            try:
                res = await asyncio.to_thread(self._search_one, p, query)
                log.debug("Ricerca '%s' su %s: %d risultati in %.0f ms", query, p.name, len(res),
                          (time.perf_counter() - started) * 1000)
                return res
            except Exception as exc:  # rete, timeout, JSON, errori dell'API
                log.warning("Ricerca su %s fallita: %s", p.label, exc)
                return exc

        outcomes = await asyncio.gather(*(run(p) for p in chosen))
        per_provider = [o for o in outcomes if isinstance(o, list)]
        failed = [p.label for p, o in zip(chosen, outcomes) if not isinstance(o, list)]
        # un risultato per provider a turno, così si vedono fonti diverse
        merged: list[Result] = []
        for rank in range(self.cfg.max_results):
            for results in per_provider:
                if rank < len(results) and len(merged) < self.cfg.max_results:
                    merged.append(results[rank])
        if not failed:
            self.cache[key] = (self.clock(), merged)
        return merged, failed
