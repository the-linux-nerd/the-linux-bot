"""Caricamento e validazione del file di configurazione YAML."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PERMISSIONS = ("anyone", "voice", "halfop", "op", "admin")
MATCH_TYPES = ("contains", "word", "exact", "startswith", "regex")

# Azioni supportate: nome -> descrizione breve (usata anche per la documentazione).
ACTIONS = {
    "say": "invia un messaggio (al canale/utente corrente o a 'to')",
    "reply": "alias di say",
    "msg": "alias di say",
    "notice": "invia una NOTICE (di default all'utente)",
    "me": "azione /me",
    "kick": "espelle un utente",
    "ban": "banna una maschera o un nick",
    "unban": "rimuove un ban",
    "kickban": "banna ed espelle",
    "op": "dà lo stato di operatore",
    "deop": "toglie lo stato di operatore",
    "halfop": "dà halfop",
    "dehalfop": "toglie halfop",
    "voice": "dà voice",
    "devoice": "toglie voice",
    "mode": "imposta modi arbitrari",
    "topic": "cambia il topic",
    "invite": "invita un utente in un canale",
    "join": "entra in un canale",
    "part": "esce da un canale",
    "nick": "cambia nick",
    "raw": "invia una riga IRC grezza",
    "delay": "attende N secondi",
    "help": "elenca i comandi disponibili",
    "reload": "ricarica la configurazione",
    "quit": "disconnette il bot",
    "seen": "dice quando un utente è stato visto l'ultima volta",
    "recent": "elenca chi è passato negli ultimi N giorni",
    "activity_map": "mappa ASCII dei giorni e delle ore di maggiore attività del canale",
    "memo_add": "lascia un messaggio per un utente",
    "memo_read": "consegna i messaggi in attesa per chi chiede",
    "banlist": "elenca i ban attivi",
    "baninfo": "dettagli sul ban di un nick o una maschera",
    "stats": "statistiche di funzionamento del bot",
    "explain": "il bot spiega sé stesso, un comando o un argomento",
    "search": "cerca risposte su Stack Overflow, unix.SE, ArchWiki...",
    "news": "racconta la notizia più importante dei feed (sezione news, modello OpenAI)",
    "karma_change": "dà o toglie punti karma a un utente",
    "karma_show": "mostra il karma di un utente",
    "karma_top": "classifica del karma",
    "karma_reset": "azzera il karma di un utente",
    "schedule_run": "esegue subito un'attività programmata (per provarla)",
    "db_backup": "copia di sicurezza del database",
    "channel_check": "controlla topic e descrizione dei canali e li imposta come nel file",
    "channel_create": "crea un canale (entra, lo registra su ChanServ, applica un modello) e lo ricorda",
    "channel_close": "chiude un canale creato con channel_create (DROP su ChanServ, esce, lo dimentica)",
    "channel_list": "elenca i canali gestiti e il loro stato",
    "channel_extend": "sposta la scadenza di un canale temporaneo o lo rende permanente",
}

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


def _expand_env(value: Any) -> Any:
    """Espande ``${VAR}`` e ``${VAR:-default}`` in tutte le stringhe."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            value = os.environ.get(name)
            if default is not None:
                return value if value else default  # come nella shell: ${VAR:-x} usa x anche se VAR è vuota
            if value is not None:
                return value
            raise ConfigError(f"variabile d'ambiente non definita: {name}")
        return _ENV.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _section(data: dict, key: str, path: str) -> dict:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}{key}: deve essere una mappa")
    return value


def _check_keys(data: dict, allowed: set[str], path: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{path}: chiavi sconosciute: {', '.join(sorted(map(str, unknown)))}")


def validate_actions(actions: Any, path: str) -> list[dict]:
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise ConfigError(f"{path}: deve essere una lista di azioni")
    result = []
    for i, action in enumerate(actions):
        where = f"{path}[{i}]"
        if isinstance(action, str):  # "- help" / "- reload" senza argomenti
            action = {action: None}
        if not isinstance(action, dict) or len(action) != 1:
            raise ConfigError(f"{where}: ogni azione deve avere esattamente una chiave (es. 'say: testo')")
        name = next(iter(action))
        if name not in ACTIONS:
            raise ConfigError(f"{where}: azione sconosciuta '{name}' (disponibili: {', '.join(ACTIONS)})")
        if name == "delay":
            try:
                float(action[name])
            except (TypeError, ValueError):
                raise ConfigError(f"{where}: delay richiede un numero di secondi") from None
        result.append(action)
    return result


def _sasl_mode(value: Any) -> str:
    if value is True or str(value).lower() in ("yes", "true", "on", "si", "sì"):
        return "yes"
    if value is False or value is None or str(value).lower() in ("no", "false", "off"):
        return "no"
    if str(value).lower() == "auto":
        return "auto"
    raise ConfigError("auth.sasl.enabled: deve essere true, false o auto")


def _permission(value: Any, path: str) -> str:
    value = str(value or "anyone").lower()
    if value not in PERMISSIONS:
        raise ConfigError(f"{path}: permesso '{value}' non valido (validi: {', '.join(PERMISSIONS)})")
    return value


# --------------------------------------------------------------------------- dataclass


@dataclass
class ServerConfig:
    host: str
    port: int = 6667
    tls: bool = False
    tls_verify: bool = True
    password: str | None = None
    encoding: str = "utf-8"


@dataclass
class IdentityConfig:
    nick: str
    alt_nicks: list[str] = field(default_factory=list)
    username: str = "linuxbot"
    realname: str = "The Linux Bot"
    version: str = "The Linux Bot"


@dataclass
class SaslConfig:
    mode: str = "no"      # yes | no | auto (usa SASL se il server lo offre, con la password di NickServ)
    username: str = ""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode != "no" and bool(self.password)


@dataclass
class NickServConfig:
    enabled: bool = False
    password: str = ""
    service: str = "NickServ"
    command: str = "auto"   # auto = "IDENTIFY {password}" se il nick è quello dell'account, altrimenti
                            # "IDENTIFY {account} {password}"; oppure un testo a piacere
    account: str = ""       # account registrato (default: identity.nick)
    services: str = "auto"  # auto (chiede la versione a NickServ) | anope | atheme | other
    regain: bool = True     # se il nick principale è occupato, se lo riprende dopo l'identificazione
    regain_commands: list[str] = field(default_factory=list)  # comandi personalizzati, provati in ordine
    wait: float = 15.0  # attesa massima della conferma prima di entrare comunque nei canali
    wait_for_login: bool = True  # entra nei canali solo dopo la conferma di NickServ
    success_pattern: str = (r"(?i)(you are now (identified|logged in)|password accepted|now recognized|"
                            r"sei (ora )?(stato )?identificat|password accettata|riconosciuto)")
    failure_pattern: str = r"(?i)(invalid password|incorrect password|password (errata|non valida)|not registered)"


@dataclass
class OperConfig:
    enabled: bool = False
    name: str = ""
    password: str = ""


@dataclass
class AuthConfig:
    sasl: SaslConfig = field(default_factory=SaslConfig)
    nickserv: NickServConfig = field(default_factory=NickServConfig)
    oper: OperConfig = field(default_factory=OperConfig)
    user_modes: str = ""


LOG_AREAS = ("connection", "irc", "auth", "channels", "commands", "greetings", "memory", "bans", "op",
             "events", "config", "stats")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def parse_size(value: Any) -> int:
    """``10MB``, ``500KB``, ``1GB`` o un numero di byte."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMG]?)B?\s*", str(value), re.IGNORECASE)
    if not m:
        raise ConfigError(f"dimensione non valida: {value} (es. 10MB)")
    return int(float(m.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2).upper()])


@dataclass
class LogFileConfig:
    path: str = ""
    rotate: str = "size"          # size | daily
    max_bytes: int = 10 * 1024 ** 2
    backups: int = 7


@dataclass
class AlertsConfig:
    enabled: bool = False
    level: str = "ERROR"
    to: list[str] = field(default_factory=list)   # canali e/o nick
    method: str = "notice"                         # notice | say
    min_interval: float = 300.0                    # stesso messaggio non più di una volta ogni N secondi
    max_per_hour: int = 20


@dataclass
class ChatLogConfig:
    enabled: bool = False
    directory: str = "logs/chat"
    channels: list[str] = field(default_factory=list)  # vuoto = tutti
    private: bool = False
    retention_days: int = 90                           # 0 = per sempre


@dataclass
class LoggingConfig:
    level: str = "INFO"
    console: bool = True
    format: str = "text"          # text | json
    file: LogFileConfig = field(default_factory=LogFileConfig)
    levels: dict[str, str] = field(default_factory=dict)   # livello per area
    redact: list[str] = field(default_factory=list)        # regex da nascondere
    slow_threshold: float = 0.5   # secondi: azioni/righe più lente generano un avviso
    slow_db_threshold: float = 0.05
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    chatlog: ChatLogConfig = field(default_factory=ChatLogConfig)


def _level(value: Any, path: str) -> str:
    value = str(value).upper()
    if value not in LOG_LEVELS:
        raise ConfigError(f"{path}: livello '{value}' non valido (validi: {', '.join(LOG_LEVELS)})")
    return value


def _parse_logging(data: dict) -> LoggingConfig:
    lg = _section(data, "logging", "")
    _check_keys(lg, set(LoggingConfig.__dataclass_fields__), "logging")
    cfg = LoggingConfig(
        level=_level(lg.get("level", "INFO"), "logging.level"),
        console=bool(lg.get("console", True)),
        format=str(lg.get("format", "text")).lower(),
        slow_threshold=float(lg.get("slow_threshold", 0.5)),
        slow_db_threshold=float(lg.get("slow_db_threshold", 0.05)),
    )
    if cfg.format not in ("text", "json"):
        raise ConfigError("logging.format: deve essere 'text' o 'json'")
    f = lg.get("file")
    if isinstance(f, str):
        f = {"path": f}
    f = f or {}
    if not isinstance(f, dict):
        raise ConfigError("logging.file: deve essere un percorso o una mappa")
    _check_keys(f, set(LogFileConfig.__dataclass_fields__), "logging.file")
    cfg.file = LogFileConfig(path=str(f.get("path") or ""), rotate=str(f.get("rotate", "size")).lower(),
                             max_bytes=parse_size(f.get("max_bytes", "10MB")), backups=int(f.get("backups", 7)))
    if cfg.file.rotate not in ("size", "daily", "none"):
        raise ConfigError("logging.file.rotate: deve essere 'size', 'daily' o 'none'")
    levels = _section(lg, "levels", "logging.")
    for area, lvl in levels.items():
        if area not in LOG_AREAS:
            raise ConfigError(f"logging.levels.{area}: area sconosciuta (disponibili: {', '.join(LOG_AREAS)})")
        cfg.levels[area] = _level(lvl, f"logging.levels.{area}")
    cfg.redact = [str(r) for r in _as_list(lg.get("redact"))]
    for i, rx in enumerate(cfg.redact):
        try:
            re.compile(rx)
        except re.error as exc:
            raise ConfigError(f"logging.redact[{i}]: regex non valida: {exc}") from None
    al = _section(lg, "alerts", "logging.")
    _check_keys(al, set(AlertsConfig.__dataclass_fields__), "logging.alerts")
    cfg.alerts = AlertsConfig(**{**al, "to": [str(t) for t in _as_list(al.get("to"))]})
    cfg.alerts.level = _level(cfg.alerts.level, "logging.alerts.level")
    if cfg.alerts.method not in ("notice", "say"):
        raise ConfigError("logging.alerts.method: deve essere 'notice' o 'say'")
    if cfg.alerts.enabled and not cfg.alerts.to:
        raise ConfigError("logging.alerts.to: indica almeno un canale o un nick")
    cl = _section(lg, "chatlog", "logging.")
    _check_keys(cl, set(ChatLogConfig.__dataclass_fields__), "logging.chatlog")
    cfg.chatlog = ChatLogConfig(**{**cl, "channels": [str(c) for c in _as_list(cl.get("channels"))]})
    return cfg


PERMISSION_WORDS = {
    "anyone": "tutti",
    "voice": "chi ha voice (o è operatore)",
    "halfop": "gli halfop e gli operatori del canale",
    "op": "gli operatori del canale",
    "admin": "gli amministratori del bot",
}

ABOUT_TEXTS = {
    "redirect": "{nick}: ti ho spiegato tutto in privato.",
    "unknown": "Non so spiegarti \"{topic}\". Puoi chiedermi di: {topics}.",
    "features_header": "Ecco cosa so fare:",
    "commands_hint": "Scrivi {prefix}help per l'elenco dei comandi, oppure \"{botnick}: come si usa <comando>?\" "
                     "o {prefix}info <comando|argomento> per i dettagli.",
    "topics_hint": "Altri argomenti: {topics}.",
    "examples": "Esempi: {examples}",
    "aliases": "Puoi scriverlo anche: {aliases}.",
    "who_can": "Chi può usarlo: {who}.",
    "not_for_you": "Questo comando non puoi usarlo: è riservato, lo usano {who}.",
    "private_only": "Si usa solo nei canali, non in privato.",
    "help_anyone": "Comandi: {commands}",
    "help_voice": "Per chi ha voice: {commands}",
    "help_halfop": "Per gli halfop: {commands}",
    "help_op": "Per gli operatori: {commands}",
    "help_admin": "Per gli admin del bot: {commands}",
    "help_hint": "Per sapere come si usa un comando: {prefix}help <comando> (es. {prefix}help {example}). "
                 "Per sapere chi sono e cosa so fare: \"{botnick}: chi sei?\"",
}

# Argomenti automatici: chiave -> (alias, testo). Compaiono solo se la funzione è attiva.
AUTO_TOPICS = {
    "saluti": (["saluto", "benvenuto"],
               "Saluto chi entra{greet_where}."),
    "memoria": (["seen", "visto", "chi è passato", "passati"],
                "Ricordo chi passa nei canali. Chiedimi \"quando è passato mario?\" oppure "
                "\"chi è passato negli ultimi 3 giorni?\"{memory_cmds}."),
    "messaggi": (["memo", "lascia", "messaggio"],
                 "Posso riferire un messaggio a chi non c'è: \"{botnick}: lascia un messaggio a mario: testo\"{memo_cmds}. "
                 "Glielo consegno quando entra, quando scrive o quando chiede \"ci sono messaggi per me?\"."),
    "ricerca": (["cerca", "stackoverflow", "stack overflow", "search"],
                "Cerco risposte tecniche su {providers}: scrivi \"?? domanda\" oppure "
                "\"{botnick}: come faccio a ...?\"{search_cmds}. In inglese si trova di più."),
    "karma": (["punti", "++", "classifica"],
              "Tengo il karma del canale: scrivi nick++ (o \"grazie nick\") per ringraziare chi ti ha aiutato, "
              "nick-- per toglierlo{karma_cmds}. Il karma è permanente, ma un ban lo azzera."),
    "domande": (["risposte", "faq"], "Rispondo da solo ad alcune domande: {questions}."),
    "moderazione": (["ban", "kick", "tempban", "moderare"],
                    "Aiuto gli operatori a moderare: ban anche a tempo (si tolgono da soli), elenco e dettagli dei ban, "
                    "kick, op e voice{mod_cmds}. Gli owner del canale e gli admin del bot sono immuni."),
}


@dataclass
class AboutConfig:
    intro: list[str] = field(default_factory=lambda: [
        "Ciao {nick}! Sono {botnick}, un bot IRC che dà una mano agli utenti{in_channel}."])
    auto_features: bool = True        # aggiunge l'elenco delle funzioni attive
    method: str = "private"           # private (query) | notice | say
    inline_max_lines: int = 2         # risposte così corte restano dove è stata fatta la domanda
    topics: dict[str, dict] = field(default_factory=dict)  # argomenti personalizzati
    texts: dict[str, str] = field(default_factory=lambda: dict(ABOUT_TEXTS))


def _parse_about(data: dict) -> AboutConfig:
    ab = _section(data, "about", "")
    _check_keys(ab, set(AboutConfig.__dataclass_fields__), "about")
    cfg = AboutConfig()
    if "intro" in ab:
        cfg.intro = [str(x) for x in _as_list(ab["intro"])]
    cfg.auto_features = bool(ab.get("auto_features", True))
    cfg.method = str(ab.get("method", "private")).lower()
    if cfg.method not in ("private", "notice", "say"):
        raise ConfigError("about.method: deve essere 'private', 'notice' o 'say'")
    cfg.inline_max_lines = int(ab.get("inline_max_lines", 2))
    for name, topic in _section(ab, "topics", "about.").items():
        where = f"about.topics.{name}"
        if isinstance(topic, (str, list)):
            topic = {"text": topic}
        if not isinstance(topic, dict) or "text" not in topic:
            raise ConfigError(f"{where}: deve essere un testo o una mappa con 'text'")
        _check_keys(topic, {"text", "aliases"}, where)
        cfg.topics[str(name).lower()] = {
            "text": [str(t) for t in _as_list(topic["text"])],
            "aliases": [str(a).lower() for a in _as_list(topic.get("aliases"))],
        }
    texts = _section(ab, "texts", "about.")
    _check_keys(texts, set(ABOUT_TEXTS), "about.texts")
    cfg.texts = {**ABOUT_TEXTS, **{k: str(v) for k, v in texts.items()}}
    return cfg


SE_HOSTS = {  # siti Stack Exchange -> dominio, per i link brevi /q/<id>
    "stackoverflow": "stackoverflow.com", "it.stackoverflow": "it.stackoverflow.com",
    "unix": "unix.stackexchange.com", "askubuntu": "askubuntu.com", "superuser": "superuser.com",
    "serverfault": "serverfault.com", "security": "security.stackexchange.com",
}

SEARCH_TEXTS = {
    "result": "[{label}] {title} {meta}{link}",
    "meta": "({accepted}{votes}, {replies}) ",
    "video_meta": "({channel}, {date}) ",
    "accepted": "✔ ",
    "none": "{nick}: non ho trovato niente per \"{query}\". Prova a riformulare, magari in inglese.",
    "error": "{nick}: la ricerca non è disponibile in questo momento, riprova più tardi.",
    "partial": "(non ho potuto cercare su: {failed})",
    "usage": "{nick}: cosa devo cercare? Es. {prefix}so come montare un disco usb",
    "too_short": "{nick}: dammi qualche parola in più da cercare.",
}


@dataclass
class SearchProvider:
    name: str
    type: str                 # stackexchange | mediawiki | youtube
    label: str = ""
    site: str = ""            # stackexchange: es. stackoverflow, unix, askubuntu, it.stackoverflow
    api: str = ""             # mediawiki: es. https://wiki.archlinux.org/api.php
    host: str = ""
    channels: list[str] = field(default_factory=list)  # youtube: handle (@nome) o ID (UC...)
    api_key: str = ""         # youtube: chiave YouTube Data API v3 (senza: solo gli ultimi 15 video, via feed)
    order: str = "relevance"  # youtube: relevance | date


DEFAULT_NEWS_FEEDS = [
    "https://lwn.net/headlines/rss",
    "https://www.phoronix.com/rss.php",
    "https://9to5linux.com/feed",
    "https://linuxiac.com/feed/",
    "https://www.omgubuntu.co.uk/feed",
    "https://itsfoss.com/rss/",
    "https://www.linuxtoday.com/feed/",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://www.bleepingcomputer.com/feed/",
    "https://krebsonsecurity.com/feed/",
    "https://www.schneier.com/feed/atom/",
    "https://www.securityweek.com/feed/",
    "https://www.darkreading.com/rss.xml",
    "https://hackaday.com/blog/feed/",
]


@dataclass
class NewsConfig:
    enabled: bool = False
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_FEEDS))
    model: str = "gpt-5-mini"
    api_key_file: str = ""            # file con la chiave OpenAI; vuoto = variabile OPENAI_API_KEY
    max_age_hours: float = 12.0       # solo le voci uscite nelle ultime N ore
    max_candidates: int = 80          # voci passate al modello, le più recenti
    timeout: float = 15.0             # per feed (la richiesta al modello ha il quadruplo)
    text: str = "📰 {title}\n{summary}\n🔗 {source}: {link}"


def _parse_news(data: dict) -> NewsConfig:
    sd = _section(data, "news", "")
    _check_keys(sd, set(NewsConfig.__dataclass_fields__), "news")
    cfg = NewsConfig(**sd)
    if not isinstance(cfg.feeds, list) or not all(isinstance(u, str) and u.startswith("http") for u in cfg.feeds):
        raise ConfigError("news.feeds: deve essere una lista di URL")
    cfg.max_age_hours, cfg.timeout = float(cfg.max_age_hours), float(cfg.timeout)
    cfg.max_candidates = int(cfg.max_candidates)
    return cfg


@dataclass
class SearchConfig:
    enabled: bool = False
    timeout: float = 8.0
    cache_minutes: float = 60.0
    max_results: int = 3
    min_query_length: int = 3
    only_answered: bool = True
    stackexchange_key: str = ""
    providers: list[SearchProvider] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=lambda: dict(SEARCH_TEXTS))


DEFAULT_PROVIDERS = [
    {"name": "unix", "type": "stackexchange", "site": "unix", "label": "unix.SE"},
    {"name": "so", "type": "stackexchange", "site": "stackoverflow", "label": "SO"},
    {"name": "askubuntu", "type": "stackexchange", "site": "askubuntu", "label": "AskUbuntu"},
    {"name": "archwiki", "type": "mediawiki", "api": "https://wiki.archlinux.org/api.php", "label": "ArchWiki"},
]


def _parse_search(data: dict) -> SearchConfig:
    sd = _section(data, "search", "")
    _check_keys(sd, set(SearchConfig.__dataclass_fields__), "search")
    cfg = SearchConfig(**{k: v for k, v in sd.items() if k not in ("providers", "texts")})
    cfg.stackexchange_key = str(cfg.stackexchange_key or "")
    providers = []
    names = set()
    for i, p in enumerate(_as_list(sd.get("providers")) or DEFAULT_PROVIDERS):
        where = f"search.providers[{i}]"
        if not isinstance(p, dict):
            raise ConfigError(f"{where}: deve essere una mappa")
        _check_keys(p, {"name", "type", "label", "site", "api", "channels", "api_key", "order"}, where)
        prov = SearchProvider(name=str(p.get("name") or p.get("site") or f"p{i}"), type=str(p.get("type", "")),
                              label=str(p.get("label") or p.get("name") or p.get("site") or ""),
                              site=str(p.get("site", "")), api=str(p.get("api", "")),
                              channels=[str(c).strip() for c in _as_list(p.get("channels"))],
                              api_key=str(p.get("api_key") or ""), order=str(p.get("order", "relevance")).lower())
        if prov.type == "stackexchange":
            if not prov.site:
                raise ConfigError(f"{where}: per stackexchange serve 'site' (es. stackoverflow, unix)")
            prov.host = SE_HOSTS.get(prov.site, f"{prov.site}.stackexchange.com")
        elif prov.type == "mediawiki":
            if not prov.api.startswith(("https://", "http://")):
                raise ConfigError(f"{where}: per mediawiki serve 'api' (URL di api.php)")
        elif prov.type == "youtube":
            if not prov.channels:
                raise ConfigError(f"{where}: per youtube serve 'channels' (es. ['@the-linux-nerd'])")
            for ch in prov.channels:
                if not re.fullmatch(r"@[\w.\-]{3,30}|UC[\w\-]{22}", ch):
                    raise ConfigError(f"{where}.channels: '{ch}' non è un handle (@nome) né un ID di canale (UC...)")
            if prov.order not in ("relevance", "date"):
                raise ConfigError(f"{where}.order: deve essere 'relevance' o 'date'")
            prov.label = str(p.get("label") or "YouTube")
        else:
            raise ConfigError(f"{where}.type: deve essere 'stackexchange', 'mediawiki' o 'youtube'")
        if prov.name in names:
            raise ConfigError(f"{where}: nome '{prov.name}' duplicato")
        names.add(prov.name)
        providers.append(prov)
    cfg.providers = providers
    texts = _section(sd, "texts", "search.")
    _check_keys(texts, set(SEARCH_TEXTS), "search.texts")
    cfg.texts = {**SEARCH_TEXTS, **{k: str(v) for k, v in texts.items()}}
    return cfg


KARMA_TEXTS = {
    "changed": "{target} ha ora {points} karma ({sign}1 da {nick})",
    "self": "{nick}: il karma non si dà da soli ;)",
    "bot": "Grazie {nick}, ma io non gioco: il karma è per gli umani.",
    "cooldown": "Hai già dato karma a {target} da poco: riprova fra {wait}.",
    "daily_limit": "Per oggi hai già dato {limit} punti karma, riprova domani.",
    "show": "{target} ha {points} karma (+{plus}/-{minus}){rank}.",
    "show_rank": ", {rank}° su {total}{where}",
    "show_none": "{target} non ha ancora punti karma.",
    "top": "Classifica karma{where}: {list}",
    "top_item": "{pos}. {nick} ({score})",
    "top_empty": "Nessuno ha ancora punti karma{where}.",
    "reset": "Karma di {target} azzerato (era {old}).",
    "reset_none": "{target} non ha punti karma da azzerare.",
    "reset_on_ban": "Karma di {target} azzerato per il ban (era {old}).",
}


@dataclass
class KarmaConfig:
    enabled: bool = False
    scope: str = "channel"            # channel = classifica per canale | global = una sola per tutti
    channels: list[str] = field(default_factory=list)  # vuoto = tutti
    plusplus: bool = True             # riconosce "nick++" e "nick--" nei messaggi
    allow_negative: bool = True       # permette "nick--"
    require_present: bool = True      # il destinatario deve essere nel canale (evita c++, g++, i++...)
    ignore_words: list[str] = field(default_factory=lambda: ["c", "g", "i", "j", "k", "n", "x", "y"])
    max_per_message: int = 3
    cooldown: float = 3600.0          # stesso donatore -> stesso destinatario
    daily_limit: int = 20             # punti che un utente può dare in 24 ore
    reset_on_ban: bool = True         # il karma si azzera quando l'utente viene bannato
    announce: bool = True             # annuncia nel canale ogni cambio
    announce_reset: bool = False      # annuncia l'azzeramento per ban
    top_size: int = 10
    texts: dict[str, str] = field(default_factory=lambda: dict(KARMA_TEXTS))


def _parse_karma(data: dict) -> KarmaConfig:
    kd = _section(data, "karma", "")
    _check_keys(kd, set(KarmaConfig.__dataclass_fields__), "karma")
    cfg = KarmaConfig(**{k: v for k, v in kd.items() if k != "texts"})
    cfg.scope = str(cfg.scope).lower()
    if cfg.scope not in ("channel", "global"):
        raise ConfigError("karma.scope: deve essere 'channel' o 'global'")
    cfg.channels = [str(c) for c in _as_list(cfg.channels)]
    cfg.ignore_words = [str(w).lower() for w in _as_list(cfg.ignore_words)]
    texts = _section(kd, "texts", "karma.")
    _check_keys(texts, set(KARMA_TEXTS), "karma.texts")
    cfg.texts = {**KARMA_TEXTS, **{k: str(v) for k, v in texts.items()}}
    return cfg


WEEKDAYS = {  # lunedì = 0
    "lun": 0, "lunedi": 0, "lunedì": 0, "mon": 0, "monday": 0,
    "mar": 1, "martedi": 1, "martedì": 1, "tue": 1, "tuesday": 1,
    "mer": 2, "mercoledi": 2, "mercoledì": 2, "wed": 2, "wednesday": 2,
    "gio": 3, "giovedi": 3, "giovedì": 3, "thu": 3, "thursday": 3,
    "ven": 4, "venerdi": 4, "venerdì": 4, "fri": 4, "friday": 4,
    "sab": 5, "sabato": 5, "sat": 5, "saturday": 5,
    "dom": 6, "domenica": 6, "sun": 6, "sunday": 6,
}


@dataclass
class ScheduleConfig:
    name: str
    at: list[str] = field(default_factory=list)      # orari "HH:MM" (ogni giorno, o nei giorni scelti)
    every: float = 0.0                                # oppure ogni N secondi (da durate come "30m", "2h")
    days: list[int] = field(default_factory=list)     # vuoto = tutti i giorni
    channels: list[str] = field(default_factory=list) # vuoto = tutti i canali in cui è il bot
    actions: list[dict] = field(default_factory=list)
    enabled: bool = True
    catch_up: float = 600.0   # se l'orario è passato da meno di così (bot spento/disconnesso), esegue appena può
    late_text: str = " (prevista alle {scheduled})"  # variabile {late} nei testi, vuota se puntuale
    per_channel: bool = True  # False: le azioni girano una volta sola, senza canale (es. backup)


def _parse_schedules(data: dict) -> list[ScheduleConfig]:
    from .memory import parse_duration

    result = []
    names = set()
    for i, sd in enumerate(_as_list(data.get("schedules"))):
        where = f"schedules[{i}]"
        if not isinstance(sd, dict):
            raise ConfigError(f"{where}: deve essere una mappa")
        _check_keys(sd, {"name", "at", "every", "days", "channels", "actions", "enabled", "catch_up", "late_text",
                         "per_channel"}, where)
        name = str(sd.get("name") or f"schedule{i}")
        if name in names:
            raise ConfigError(f"{where}: nome '{name}' duplicato")
        names.add(name)
        times = []
        for t in _as_list(sd.get("at")):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(t).strip())
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise ConfigError(f"{where}.at: orario non valido '{t}' (formato HH:MM, es. 00:00 o 18:30)")
            times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        every = 0.0
        if sd.get("every") is not None:
            parsed = parse_duration(str(sd["every"]))
            if not parsed or parsed < 60:
                raise ConfigError(f"{where}.every: durata non valida '{sd['every']}' (minimo 1m, es. 30m, 2h, 1d)")
            every = parsed
        if bool(times) == bool(every):
            raise ConfigError(f"{where}: indica 'at' (orari) oppure 'every' (intervallo), non entrambi né nessuno")
        days = []
        for d in _as_list(sd.get("days")):
            key = str(d).strip().lower()
            if key not in WEEKDAYS:
                raise ConfigError(f"{where}.days: giorno '{d}' non valido (es. lun, mar, ..., dom)")
            days.append(WEEKDAYS[key])
        actions = validate_actions(sd.get("actions"), f"{where}.actions")
        if not actions:
            raise ConfigError(f"{where}: serve almeno un'azione")
        catch_up = 600.0
        if sd.get("catch_up") is not None:
            parsed = parse_duration(str(sd["catch_up"]))
            if parsed is None:
                raise ConfigError(f"{where}.catch_up: durata non valida '{sd['catch_up']}' (es. 10m, 12h, 0)")
            catch_up = parsed
        result.append(ScheduleConfig(name=name, at=times, every=every, days=sorted(set(days)),
                                     channels=[str(c) for c in _as_list(sd.get("channels"))],
                                     actions=actions, enabled=bool(sd.get("enabled", True)),
                                     catch_up=catch_up,
                                     late_text=str(sd.get("late_text", ScheduleConfig.late_text)),
                                     per_channel=bool(sd.get("per_channel", True))))
    return result


@dataclass
class ChannelManagementConfig:
    default_template: str = ""        # modello usato da !crea se non se ne indica uno
    register: bool = True             # registra su ChanServ i canali creati
    register_command: str = "REGISTER {channel}"
    drop: bool = True                 # !chiudi toglie la registrazione da ChanServ (DROP)
    drop_command: str = "DROP {channel}"
    confirm_seconds: float = 60.0     # tempo per confermare !chiudi
    max_channels: int = 20            # canali creati da IRC al massimo
    event_template: str = ""          # modello per !evento (canali temporanei)
    warn_before: str = "1h"           # avviso nel canale temporaneo prima della chiusura ("" = niente)
    warn_message: str = "⏰ {channel} è un canale temporaneo: chiude {expires_in} ({expires})."
    close_message: str = "{channel} chiude adesso: grazie a tutti per aver partecipato!"


CHANNEL_TEMPLATE_KEYS = {"key", "owners", "topic", "topic_lock", "description", "description_command",
                         "modes", "modes_lock"}


def _parse_channel_management(data: dict) -> tuple[ChannelManagementConfig, dict[str, dict]]:
    from .memory import parse_duration

    cm = _section(data, "channel_management", "")
    _check_keys(cm, set(ChannelManagementConfig.__dataclass_fields__), "channel_management")
    cfg = ChannelManagementConfig(**cm)
    cfg.warn_before = str(cfg.warn_before or "")
    if cfg.warn_before and parse_duration(cfg.warn_before) is None:
        raise ConfigError(f"channel_management.warn_before: durata non valida '{cfg.warn_before}' (es. 1h, 30m)")
    templates: dict[str, dict] = {}
    for name, tpl in _section(data, "channel_templates", "").items():
        where = f"channel_templates.{name}"
        if not isinstance(tpl, dict):
            raise ConfigError(f"{where}: deve essere una mappa (topic, modes, description, owners, duration...)")
        _check_keys(tpl, CHANNEL_TEMPLATE_KEYS | {"duration"}, where)
        if tpl.get("duration") is not None and parse_duration(str(tpl["duration"])) is None:
            raise ConfigError(f"{where}.duration: durata non valida '{tpl['duration']}' (es. 6h, 3d, 1w, perm)")
        # valida il modello come se fosse un canale vero
        _parse_channel({"name": "#modello", **{k: v for k, v in tpl.items() if k != "duration"}}, where)
        templates[str(name).lower()] = dict(tpl)
    for key in ("default_template", "event_template"):
        value = str(getattr(cfg, key) or "").lower()
        if value and value not in templates:
            raise ConfigError(f"channel_management.{key}: modello '{value}' non definito in channel_templates")
        setattr(cfg, key, value)
    return cfg, templates


def _parse_channel(ch: dict, where: str) -> "ChannelConfig":
    """Un canale (o un modello di canale) dalla configurazione, con tutti i controlli."""
    _check_keys(ch, {"name"} | CHANNEL_TEMPLATE_KEYS, where)
    name = str(ch["name"])
    if not re.fullmatch(r"[#&][^\s,\x07]{1,49}", name):
        raise ConfigError(f"{where}: nome di canale non valido '{name}' (es. #linux)")
    modes = ch.get("modes")
    if modes is not None:
        modes = " ".join(str(modes).split())
        tokens = modes.split()
        if not tokens or not re.fullmatch(r"[+-][A-Za-z+-]*", tokens[0]):
            raise ConfigError(f"{where}.modes: usa la sintassi IRC, es. \"+ntC-s\" o \"+ntl 50\"")
        letters = "".join(t for t in tokens if re.fullmatch(r"[+-][A-Za-z+-]*", t))
        bad = sorted(set(letters) & set("ovhaqbeI"))
        if bad:
            raise ConfigError(f"{where}.modes: {''.join(bad)} non va qui: i modi degli utenti "
                              "(o, v, h...) e le liste (b, e, I) hanno la loro gestione")
    topic = ch.get("topic")
    if topic is not None and len(str(topic).encode()) > 390:
        raise ConfigError(f"{where}.topic: troppo lungo (i server accettano circa 300-390 byte)")
    return ChannelConfig(
        name, ch.get("key") or None, [str(m) for m in _as_list(ch.get("owners"))],
        topic=" ".join(str(topic).split()) if topic is not None else None,
        topic_lock=bool(ch.get("topic_lock", False)),
        description=" ".join(str(ch["description"]).split()) if ch.get("description") is not None else None,
        description_command=str(ch.get("description_command") or ""),
        modes=modes, modes_lock=bool(ch.get("modes_lock", False)))


def channel_from_template(name: str, template: dict, key: str = "") -> "ChannelConfig":
    template = {k: v for k, v in template.items() if k != "duration"}
    data = {"name": name, **template}
    if key:
        data["key"] = key
    return _parse_channel(data, f"canale {name}")


@dataclass
class SelfOpConfig:
    enabled: bool = False
    method: str = "chanserv"         # chanserv | samode | auto (samode se IRC operator, altrimenti chanserv)
    service: str = "ChanServ"
    command: str = "OP {channel} {botnick}"
    samode: str = "SAMODE {channel} +o {botnick}"
    delay: float = 2.0               # attesa dopo l'ingresso (ChanServ spesso dà l'op da solo)
    retry: float = 30.0              # secondi fra un tentativo e l'altro
    max_retries: int = 3
    on_deop: bool = True             # se gli tolgono l'op, lo richiede
    channels: list[str] = field(default_factory=list)  # vuoto = tutti


def _parse_self_op(data: dict) -> SelfOpConfig:
    so = _section(data, "self_op", "")
    _check_keys(so, set(SelfOpConfig.__dataclass_fields__), "self_op")
    cfg = SelfOpConfig(**so)
    cfg.method = str(cfg.method).lower()
    if cfg.method not in ("chanserv", "samode", "auto"):
        raise ConfigError("self_op.method: deve essere 'chanserv', 'samode' o 'auto'")
    cfg.channels = [str(c) for c in _as_list(cfg.channels)]
    return cfg


@dataclass
class ChannelConfig:
    name: str
    key: str | None = None
    owners: list[str] = field(default_factory=list)  # maschere immuni a ban e kick in questo canale
    topic: str | None = None        # titolo del canale da garantire (None = non toccarlo)
    topic_lock: bool = False        # se qualcuno cambia il topic, il bot rimette quello configurato
    description: str | None = None  # descrizione registrata presso ChanServ (None = non toccarla)
    description_command: str = ""   # comando per ChanServ; vuoto = automatico secondo i services
    modes: str | None = None        # modi da garantire, sintassi IRC: "+ntC-s", "+ntl 50", "+k chiave"
    modes_lock: bool = False        # se qualcuno li cambia, il bot li rimette
    managed: bool = False           # creato con !crea (sta nel database, non nel file)
    template: str = ""              # modello usato per crearlo
    expires: float | None = None    # canali temporanei: quando chiude (timestamp)


@dataclass
class BehaviorConfig:
    command_prefixes: list[str] = field(default_factory=lambda: ["!"])
    answer_when_addressed: bool = True  # "TheLinuxBot: help" funziona come "!help"
    respond_to_private: bool = True
    ignore: list[str] = field(default_factory=list)
    rejoin_on_kick: bool = True
    rejoin_delay: float = 5.0
    reconnect_delay: float = 10.0
    reconnect_max_delay: float = 300.0
    flood_burst: int = 4
    flood_rate: float = 1.0  # secondi fra un messaggio e l'altro dopo il burst
    max_line_bytes: int = 400
    unknown_command_reply: str | None = None
    permission_denied_reply: str | None = "{nick}: non hai i permessi per usare {command}."
    cooldown_reply: str | None = None
    timezone: str = ""  # fuso orario per orari, {date} e {time} (es. Europe/Rome); vuoto = quello del sistema


@dataclass
class GreetingRule:
    match: list[str]
    channels: list[str] = field(default_factory=list)
    messages: list[str] | None = None
    method: str | None = None
    actions: list[dict] = field(default_factory=list)
    skip: bool = False


@dataclass
class GreetingsConfig:
    enabled: bool = False
    channels: list[str] = field(default_factory=list)  # vuoto = tutti
    delay: float = 1.0
    cooldown: float = 3600.0
    method: str = "say"  # say | notice
    messages: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    rules: list[GreetingRule] = field(default_factory=list)


@dataclass
class ResponseConfig:
    name: str
    patterns: list[str]
    match: str = "contains"
    description: str = ""  # per le spiegazioni del bot: es. "consigli sulle distro"
    case_sensitive: bool = False
    reply: Any = None
    method: str = "say"
    actions: list[dict] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    private: bool = True
    addressed: bool = False
    cooldown: float = 30.0
    probability: float = 1.0
    permission: str = "anyone"
    stop: bool = True
    compiled: list[re.Pattern] = field(default_factory=list, repr=False)

    def compile(self) -> None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        self.compiled = []
        for p in self.patterns:
            if self.match == "regex":
                rx = p
            elif self.match == "contains":
                rx = re.escape(p)
            elif self.match == "word":
                rx = r"(?<!\w)" + re.escape(p) + r"(?!\w)"
            elif self.match == "exact":
                rx = r"^\s*" + re.escape(p) + r"\s*[?!.]*\s*$"
            else:  # startswith
                rx = r"^\s*" + re.escape(p)
            self.compiled.append(re.compile(rx, flags))


@dataclass
class CommandConfig:
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    usage: str = ""
    permission: str = "anyone"
    min_args: int = 0
    reply: Any = None
    method: str = "say"
    actions: list[dict] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    private: bool = True
    cooldown: float = 0.0
    hidden: bool = False
    enabled: bool = True
    args_pattern: str = ""  # regex che gli argomenti devono rispettare (es. '^\\d+$'), altrimenti "Uso: ..."
    details: list[str] = field(default_factory=list)   # spiegazione estesa (una riga per elemento)
    examples: list[str] = field(default_factory=list)  # esempi d'uso, es. "!man ls"


@dataclass
class Config:
    server: ServerConfig
    identity: IdentityConfig
    auth: AuthConfig = field(default_factory=AuthConfig)
    channels: list[ChannelConfig] = field(default_factory=list)
    admins: list[str] = field(default_factory=list)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    variables: dict[str, str] = field(default_factory=dict)
    greetings: GreetingsConfig = field(default_factory=GreetingsConfig)
    responses: list[ResponseConfig] = field(default_factory=list)
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    events: dict[str, list[dict]] = field(default_factory=dict)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    memory: MemoryConfig = field(default_factory=lambda: MemoryConfig())
    bans: BansConfig = field(default_factory=lambda: BansConfig())
    self_op: SelfOpConfig = field(default_factory=SelfOpConfig)
    about: AboutConfig = field(default_factory=AboutConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    karma: KarmaConfig = field(default_factory=KarmaConfig)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    channel_management: ChannelManagementConfig = field(default_factory=ChannelManagementConfig)
    channel_templates: dict[str, dict] = field(default_factory=dict)
    path: Path | None = None
    data_dir: Path | None = None  # se impostata, i percorsi relativi (database, log, backup) partono da qui

    def resolve(self, path: str) -> Path:
        """Percorsi relativi: dalla cartella dati (``--data-dir``) se c'è, altrimenti da quella della config."""
        p = Path(path).expanduser()
        if p.is_absolute() or str(p) == ":memory:":
            return p
        if self.data_dir is not None:
            return self.data_dir / p
        if self.path is not None:
            return self.path.parent / p
        return p

    @property
    def database_path(self) -> Path:
        """Percorso del database (``memory.database``), risolto come gli altri percorsi relativi."""
        return self.resolve(self.memory.database)

    def immune_masks(self, channel: str = "") -> list[str]:
        """Maschere immuni a ban e kick: admin, ``bans.immune`` e owner del canale."""
        from .irc import irc_lower

        masks = list(self.admins) + list(self.bans.immune)
        for ch in self.channels:
            if not channel or irc_lower(ch.name) == irc_lower(channel):
                masks.extend(ch.owners)
        return masks

    def find_command(self, name: str) -> CommandConfig | None:
        name = name.lower()
        for cmd in self.commands.values():
            if cmd.enabled and (cmd.name == name or name in cmd.aliases):
                return cmd
        return None


MEMORY_TEXTS = {
    "seen_here": "{who} è qui adesso, in {where}.",
    "seen": "{who} è stato visto l'ultima volta {ago} ({when}) in {where}, {what}.",
    "seen_never": "Non ho mai visto {who}.",
    "seen_self": "Sei proprio qui, {nick}!",
    "seen_bot": "Io sono sempre qui!",
    "seen_usage": "Di chi vuoi sapere? Uso: {prefix}seen <nick>",
    "event_join": "mentre entrava",
    "event_part": "mentre usciva ({detail})",
    "event_quit": "mentre si disconnetteva ({detail})",
    "event_kick": "mentre veniva espulso ({detail})",
    "event_nick": "mentre cambiava nick in {detail}",
    "event_nick_from": "dopo aver cambiato nick da {detail}",
    "event_message": "mentre scriveva",
    "event_message_text": "mentre scriveva: {detail}",
    "recent": "Passati {period} ({count}): {names}",
    "recent_none": "Non è passato nessuno {period}.",
    "recent_more": "e altri {count}",
    "activity_title": "Quando c'è gente in {where} ({count} messaggi {period}).",
    "activity_period_days": "negli ultimi {days} giorni",
    "activity_period_all": "dal {since}",
    "activity_everywhere": "tutti i canali",
    "activity_days": "Lun Mar Mer Gio Ven Sab Dom",
    "activity_hour": "ore",
    "activity_best": "Meglio venire: {best}.",
    "activity_none": "Non ho ancora contato messaggi in {where} {period}.",
    "memo_saved": "Ok {nick}, riferirò il messaggio a {recipient} appena lo vedo.",
    "memo_usage": "Uso: {prefix}lascia <nick> <messaggio>",
    "memo_self": "{nick}, non puoi lasciare un messaggio a te stesso.",
    "memo_bot": "{nick}, il messaggio per me l'ho già letto ;)",
    "memo_too_long": "{nick}, il messaggio è troppo lungo (massimo {max} caratteri).",
    "memo_full": "{nick}, {recipient} ha già troppi messaggi in attesa.",
    "memo_delivered": "{recipient}, {sender} ti ha lasciato un messaggio {ago}: {text}",
    "memo_none": "Nessun messaggio per te, {nick}.",
}


@dataclass
class MemosConfig:
    deliver_on_join: bool = True
    deliver_on_message: bool = True  # anche quando il destinatario scrive qualcosa
    method: str = "private"          # private (query) | notice | say (nel canale)
    delay: float = 3.0
    max_length: int = 300
    max_pending: int = 10            # per destinatario
    expire_days: float = 60.0


@dataclass
class MemoryConfig:
    enabled: bool = False
    database: str = "linuxbot.db"
    retention_days: float = 365.0
    store_messages: bool = False     # salva il testo dell'ultimo messaggio (per "seen")
    default_days: float = 7.0
    max_names: int = 25
    memos: MemosConfig = field(default_factory=MemosConfig)
    texts: dict[str, str] = field(default_factory=lambda: dict(MEMORY_TEXTS))


def _parse_memory(data: dict) -> MemoryConfig:
    mem = _section(data, "memory", "")
    _check_keys(mem, {"enabled", "database", "retention_days", "store_messages", "default_days",
                      "max_names", "memos", "texts"}, "memory")
    memos_d = _section(mem, "memos", "memory.")
    _check_keys(memos_d, set(MemosConfig.__dataclass_fields__), "memory.memos")
    memos = MemosConfig(**memos_d)
    memos.method = str(memos.method).lower()
    if memos.method not in ("private", "notice", "say"):
        raise ConfigError("memory.memos.method: deve essere 'private', 'notice' o 'say'")
    texts_d = _section(mem, "texts", "memory.")
    _check_keys(texts_d, set(MEMORY_TEXTS), "memory.texts")
    defaults = MemoryConfig()
    return MemoryConfig(
        enabled=bool(mem.get("enabled", False)),
        database=str(mem.get("database", defaults.database)),
        retention_days=float(mem.get("retention_days", defaults.retention_days)),
        store_messages=bool(mem.get("store_messages", False)),
        default_days=float(mem.get("default_days", defaults.default_days)),
        max_names=int(mem.get("max_names", defaults.max_names)),
        memos=memos,
        texts={**MEMORY_TEXTS, **{k: str(v) for k, v in texts_d.items()}},
    )


BAN_MASKS = {
    "host": "*!*@{host}",
    "userhost": "*!{ident}@{host}",
    "nick": "{nick}!*@*",
    "full": "{nick}!{user}@{host}",
}

BAN_TEXTS = {
    "kick_reason": "{reason}",
    "kick_reason_temp": "{reason} (ban per {duration})",
    "default_reason": "Bannato da {nick}",
    "ban_done": "Ban impostato su {mask} in {channel}, {expiry}.",
    "unban_done": "Ban rimosso: {mask} in {channel}.",
    "unban_unknown": "Non ho ban registrati per {who} in {channel}: provo a togliere {mask}.",
    "bad_duration": "Durata non valida: {duration}. Esempi: 30m, 2h, 1d, 1w, 1d12h, perm.",
    "too_long": "La durata massima di un ban è {max}.",
    "no_channel": "In quale canale? Indica il canale, es. #linux.",
    "list_header": "Ban attivi in {channel} ({count}):",
    "list_item": "#{id} {mask}{who} da {setter} {ago}, {expiry}{why}",
    "list_more": "...e altri {count}. Usa {prefix}baninfo <nick|maschera|#id> per i dettagli.",
    "list_empty": "Nessun ban attivo in {channel}.",
    "info": "#{id} {mask} in {channel}{who}: messo da {setter} il {when} ({ago}), {expiry}{why}",
    "info_removed": "Nessun ban attivo per {who}. L'ultimo (#{id} {mask}) è stato tolto {removed_how} {removed_ago}.",
    "info_none": "Nessun ban registrato per {who}.",
    "expiry_permanent": "permanente",
    "expiry_in": "scade fra {remaining}",
    "enforce_reason": "Sei bannato da questo canale{why}",
    "immune": "{who} è immune a ban e kick in {channel}.",
}


@dataclass
class BansConfig:
    enabled: bool = False
    mask: str = "host"               # host | userhost | nick | full | modello con {nick} {ident} {user} {host}
    enforce: bool = True             # rimette i ban tolti a mano ed espelle chi rientra
    track_manual: bool = True        # registra anche i ban messi a mano dagli op
    check_interval: float = 30.0     # ogni quanti secondi controllare i ban scaduti
    default_duration: str = ""       # vuoto = permanente
    max_duration: str = ""           # vuoto = nessun limite
    list_max: int = 10
    immune: list[str] = field(default_factory=list)  # maschere immuni in tutti i canali
    unban_immune: bool = True        # toglie i ban messi da altri che colpiscono un immune
    texts: dict[str, str] = field(default_factory=lambda: dict(BAN_TEXTS))

    @property
    def mask_template(self) -> str:
        return BAN_MASKS.get(self.mask, self.mask)


def _parse_bans(data: dict) -> BansConfig:
    from .memory import parse_duration

    b = _section(data, "bans", "")
    _check_keys(b, set(BansConfig.__dataclass_fields__), "bans")
    texts_d = _section(b, "texts", "bans.")
    _check_keys(texts_d, set(BAN_TEXTS), "bans.texts")
    cfg = BansConfig(**{k: v for k, v in b.items() if k != "texts"})
    cfg.mask = str(cfg.mask)
    cfg.immune = [str(m) for m in _as_list(cfg.immune)]
    if cfg.mask not in BAN_MASKS and not ("{host}" in cfg.mask or "{nick}" in cfg.mask):
        raise ConfigError(f"bans.mask: usa {', '.join(BAN_MASKS)} oppure un modello con {{nick}}/{{host}}")
    for key in ("default_duration", "max_duration"):
        value = str(getattr(cfg, key) or "")
        setattr(cfg, key, value)
        if value and parse_duration(value) is None:
            raise ConfigError(f"bans.{key}: durata non valida '{value}' (es. 2h, 1d, 1w)")
    cfg.texts = {**BAN_TEXTS, **{k: str(v) for k, v in texts_d.items()}}
    return cfg


EVENTS = ("connect", "identified", "self_join", "self_op", "self_deop", "join", "part", "quit", "kick", "self_kick", "nick", "topic", "invite")


# --------------------------------------------------------------------------- parsing


def _method(value: Any, path: str) -> str:
    value = str(value or "say").lower()
    if value not in ("say", "notice"):
        raise ConfigError(f"{path}: method deve essere 'say' o 'notice'")
    return value


def parse_config(data: Any, path: Path | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("il file di configurazione deve contenere una mappa YAML")
    data = _expand_env(data)
    _check_keys(data, {"server", "identity", "auth", "channels", "admins", "behavior", "variables",
                       "greetings", "responses", "commands", "events", "logging", "memory", "bans", "self_op", "about", "search", "news", "karma", "schedules",
                       "channel_management", "channel_templates"}, "<radice>")

    # server
    srv = _section(data, "server", "")
    _check_keys(srv, {"host", "port", "tls", "tls_verify", "password", "encoding"}, "server")
    if not srv.get("host"):
        raise ConfigError("server.host è obbligatorio")
    server = ServerConfig(
        host=str(srv["host"]),
        port=int(srv.get("port", 6697 if srv.get("tls") else 6667)),
        tls=bool(srv.get("tls", False)),
        tls_verify=bool(srv.get("tls_verify", True)),
        password=srv.get("password") or None,
        encoding=str(srv.get("encoding", "utf-8")),
    )

    # identity
    ident = _section(data, "identity", "")
    _check_keys(ident, {"nick", "alt_nicks", "username", "realname", "version"}, "identity")
    if not ident.get("nick"):
        raise ConfigError("identity.nick è obbligatorio")
    identity = IdentityConfig(
        nick=str(ident["nick"]),
        alt_nicks=[str(n) for n in _as_list(ident.get("alt_nicks"))],
        username=str(ident.get("username", "linuxbot")),
        realname=str(ident.get("realname", "The Linux Bot")),
        version=str(ident.get("version", "The Linux Bot")),
    )

    # auth
    auth_d = _section(data, "auth", "")
    _check_keys(auth_d, {"sasl", "nickserv", "oper", "user_modes"}, "auth")
    sasl_d = _section(auth_d, "sasl", "auth.")
    _check_keys(sasl_d, {"enabled", "username", "password"}, "auth.sasl")
    ns_d = _section(auth_d, "nickserv", "auth.")
    _check_keys(ns_d, {"enabled", "password", "service", "command", "wait", "wait_for_login",
                       "success_pattern", "failure_pattern", "account", "services", "regain", "regain_commands"},
                "auth.nickserv")
    ns_defaults = NickServConfig()
    for key in ("success_pattern", "failure_pattern"):
        try:
            re.compile(str(ns_d.get(key, getattr(ns_defaults, key))))
        except re.error as exc:
            raise ConfigError(f"auth.nickserv.{key}: regex non valida: {exc}") from None
    op_d = _section(auth_d, "oper", "auth.")
    _check_keys(op_d, {"enabled", "name", "password"}, "auth.oper")
    auth = AuthConfig(
        sasl=SaslConfig(
            mode=_sasl_mode(sasl_d.get("enabled", "auto")),
            username=str(sasl_d.get("username") or ns_d.get("account") or identity.nick),
            # in modalità auto, senza password propria usa quella di NickServ
            password=str(sasl_d.get("password") or (ns_d.get("password") if ns_d.get("enabled") else "") or ""),
        ),
        nickserv=NickServConfig(
            enabled=bool(ns_d.get("enabled", False)),
            password=str(ns_d.get("password") or ""),
            service=str(ns_d.get("service", "NickServ")),
            command=str(ns_d.get("command", "auto")),
            account=str(ns_d.get("account") or identity.nick),
            services=str(ns_d.get("services", "auto")).lower(),
            regain=bool(ns_d.get("regain", True)),
            regain_commands=[str(c) for c in _as_list(ns_d.get("regain_commands"))],
            wait=float(ns_d.get("wait", ns_defaults.wait)),
            wait_for_login=bool(ns_d.get("wait_for_login", True)),
            success_pattern=str(ns_d.get("success_pattern", ns_defaults.success_pattern)),
            failure_pattern=str(ns_d.get("failure_pattern", ns_defaults.failure_pattern)),
        ),
        oper=OperConfig(
            enabled=bool(op_d.get("enabled", False)),
            name=str(op_d.get("name") or identity.nick),
            password=str(op_d.get("password") or ""),
        ),
        user_modes=str(auth_d.get("user_modes") or ""),
    )
    if auth.sasl.mode == "yes" and not auth.sasl.password:
        raise ConfigError("auth.sasl.password (o auth.nickserv.password) è obbligatoria se SASL è abilitato")
    if auth.nickserv.services not in ("auto", "anope", "atheme", "other"):
        raise ConfigError("auth.nickserv.services: deve essere auto, anope, atheme o other")
    if auth.oper.enabled and not auth.oper.password:
        raise ConfigError("auth.oper.password è obbligatoria se oper è abilitato")
    if auth.nickserv.enabled and not auth.nickserv.password:
        raise ConfigError("auth.nickserv.password è obbligatoria se nickserv è abilitato")

    # channels
    channels = []
    for i, ch in enumerate(_as_list(data.get("channels"))):
        if isinstance(ch, str):
            ch = {"name": ch}
        if not isinstance(ch, dict) or not ch.get("name"):
            raise ConfigError(f"channels[{i}]: deve essere un nome o una mappa con 'name'")
        channels.append(_parse_channel(ch, f"channels[{i}]"))
    names = [c.name.lower() for c in channels]
    if len(set(names)) != len(names):
        raise ConfigError("channels: lo stesso canale compare più volte")

    # behavior
    beh_d = _section(data, "behavior", "")
    defaults = BehaviorConfig()
    _check_keys(beh_d, set(BehaviorConfig.__dataclass_fields__) | {"command_prefix"}, "behavior")
    prefixes = beh_d.get("command_prefixes", beh_d.get("command_prefix", defaults.command_prefixes))
    behavior = BehaviorConfig(
        command_prefixes=[str(p) for p in _as_list(prefixes)],
        **{k: beh_d[k] for k in BehaviorConfig.__dataclass_fields__ if k in beh_d and k != "command_prefixes"},
    )
    behavior.ignore = [str(m) for m in _as_list(behavior.ignore)]
    behavior.timezone = str(behavior.timezone or "")
    if behavior.timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(behavior.timezone)
        except Exception:
            raise ConfigError(f"behavior.timezone: fuso orario sconosciuto '{behavior.timezone}' "
                              "(es. Europe/Rome)") from None

    # variables
    variables = {str(k): str(v) for k, v in _section(data, "variables", "").items()}

    # greetings
    gr_d = _section(data, "greetings", "")
    _check_keys(gr_d, {"enabled", "channels", "delay", "cooldown", "method", "messages", "actions", "rules"},
                "greetings")
    rules = []
    for i, r in enumerate(_as_list(gr_d.get("rules"))):
        where = f"greetings.rules[{i}]"
        if not isinstance(r, dict) or "match" not in r:
            raise ConfigError(f"{where}: ogni regola deve avere 'match'")
        _check_keys(r, {"match", "channels", "messages", "method", "actions", "skip"}, where)
        rules.append(GreetingRule(
            match=[str(m) for m in _as_list(r["match"])],
            channels=[str(c) for c in _as_list(r.get("channels"))],
            messages=[str(m) for m in _as_list(r["messages"])] if "messages" in r else None,
            method=_method(r["method"], where) if r.get("method") else None,
            actions=validate_actions(r.get("actions"), f"{where}.actions"),
            skip=bool(r.get("skip", False)),
        ))
    greetings = GreetingsConfig(
        enabled=bool(gr_d.get("enabled", False)),
        channels=[str(c) for c in _as_list(gr_d.get("channels"))],
        delay=float(gr_d.get("delay", 1.0)),
        cooldown=float(gr_d.get("cooldown", 3600.0)),
        method=_method(gr_d.get("method"), "greetings"),
        messages=[str(m) for m in _as_list(gr_d.get("messages"))],
        actions=validate_actions(gr_d.get("actions"), "greetings.actions"),
        rules=rules,
    )

    # responses
    responses = []
    resp_fields = set(ResponseConfig.__dataclass_fields__) - {"compiled"} | {"pattern"}
    for i, r in enumerate(_as_list(data.get("responses"))):
        where = f"responses[{i}]"
        if not isinstance(r, dict):
            raise ConfigError(f"{where}: deve essere una mappa")
        _check_keys(r, resp_fields, where)
        patterns = [str(p) for p in _as_list(r.get("patterns")) + _as_list(r.get("pattern"))]
        if not patterns:
            raise ConfigError(f"{where}: serve almeno un 'pattern'/'patterns'")
        match = str(r.get("match", "contains")).lower()
        if match not in MATCH_TYPES:
            raise ConfigError(f"{where}.match: deve essere uno di {', '.join(MATCH_TYPES)}")
        if r.get("reply") is None and not r.get("actions"):
            raise ConfigError(f"{where}: serve 'reply' oppure 'actions'")
        resp = ResponseConfig(
            description=str(r.get("description", "")),
            name=str(r.get("name", f"response{i}")),
            patterns=patterns,
            match=match,
            case_sensitive=bool(r.get("case_sensitive", False)),
            reply=r.get("reply"),
            method=_method(r.get("method"), where),
            actions=validate_actions(r.get("actions"), f"{where}.actions"),
            channels=[str(c) for c in _as_list(r.get("channels"))],
            private=bool(r.get("private", True)),
            addressed=bool(r.get("addressed", False)),
            cooldown=float(r.get("cooldown", 30.0)),
            probability=float(r.get("probability", 1.0)),
            permission=_permission(r.get("permission"), f"{where}.permission"),
            stop=bool(r.get("stop", True)),
        )
        try:
            resp.compile()
        except re.error as exc:
            raise ConfigError(f"{where}: regex non valida: {exc}") from None
        responses.append(resp)

    # commands
    commands: dict[str, CommandConfig] = {}
    cmd_d = data.get("commands") or {}
    if not isinstance(cmd_d, dict):
        raise ConfigError("commands: deve essere una mappa nome -> definizione")
    seen: dict[str, str] = {}
    cmd_fields = set(CommandConfig.__dataclass_fields__) - {"name"}
    for name, c in cmd_d.items():
        name = str(name).lower()
        where = f"commands.{name}"
        if c is None:
            c = {}
        if isinstance(c, (str, list)):  # forma breve: "rules: Le regole sono..."
            c = {"reply": c}
        if not isinstance(c, dict):
            raise ConfigError(f"{where}: deve essere una mappa o un testo")
        _check_keys(c, cmd_fields, where)
        if c.get("reply") is None and not c.get("actions"):
            raise ConfigError(f"{where}: serve 'reply' oppure 'actions'")
        cmd = CommandConfig(
            name=name,
            aliases=[str(a).lower() for a in _as_list(c.get("aliases"))],
            description=str(c.get("description", "")),
            usage=str(c.get("usage", "")),
            permission=_permission(c.get("permission"), f"{where}.permission"),
            min_args=int(c.get("min_args", 0)),
            reply=c.get("reply"),
            method=_method(c.get("method"), where),
            actions=validate_actions(c.get("actions"), f"{where}.actions"),
            channels=[str(ch) for ch in _as_list(c.get("channels"))],
            private=bool(c.get("private", True)),
            cooldown=float(c.get("cooldown", 0.0)),
            hidden=bool(c.get("hidden", False)),
            enabled=bool(c.get("enabled", True)),
            args_pattern=str(c.get("args_pattern", "")),
            details=[str(d) for d in _as_list(c.get("details"))],
            examples=[str(e) for e in _as_list(c.get("examples"))],
        )
        if cmd.args_pattern:
            try:
                re.compile(cmd.args_pattern)
            except re.error as exc:
                raise ConfigError(f"{where}.args_pattern: regex non valida: {exc}") from None
        for key in [cmd.name, *cmd.aliases]:
            if key in seen:
                raise ConfigError(f"{where}: '{key}' è già usato da commands.{seen[key]}")
            seen[key] = cmd.name
        commands[name] = cmd

    # events
    events: dict[str, list[dict]] = {}
    for name, actions in _section(data, "events", "").items():
        if name not in EVENTS:
            raise ConfigError(f"events.{name}: evento sconosciuto (disponibili: {', '.join(EVENTS)})")
        events[name] = validate_actions(actions, f"events.{name}")

    management, templates = _parse_channel_management(data)
    return Config(
        server=server, identity=identity, auth=auth, channels=channels,
        admins=[str(a) for a in _as_list(data.get("admins"))], behavior=behavior,
        variables=variables, greetings=greetings, responses=responses, commands=commands,
        events=events, logging=_parse_logging(data), memory=_parse_memory(data), bans=_parse_bans(data), self_op=_parse_self_op(data), about=_parse_about(data), search=_parse_search(data), news=_parse_news(data), karma=_parse_karma(data), schedules=_parse_schedules(data),
        channel_management=management, channel_templates=templates,
        path=path,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"impossibile leggere {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML non valido in {path}: {exc}") from None
    return parse_config(data, path)
