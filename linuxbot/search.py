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
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from . import __version__
from .config import SearchConfig, SearchProvider

log = logging.getLogger("linuxbot.search")

SE_API = "https://api.stackexchange.com/2.3"


@dataclass
class Result:
    provider: str
    label: str
    title: str
    link: str
    score: int | None = None
    answers: int | None = None
    accepted: bool = False


def http_get(url: str, timeout: float) -> Any:
    """GET di un URL che risponde JSON (anche compresso gzip, come fa sempre Stack Exchange)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"TheLinuxBot/{__version__} (IRC bot)",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL costruiti da noi)
        raw = resp.read(2 * 1024 * 1024)
        if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


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
        return [Result(p.name, p.label, _clean(t), link) for t, link in zip(data[1], data[3])]

    def _search_one(self, p: SearchProvider, query: str) -> list[Result]:
        if p.type == "stackexchange":
            return self._stackexchange(p, query)
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
