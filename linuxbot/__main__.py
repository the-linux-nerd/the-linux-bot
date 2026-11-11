"""Avvio da riga di comando: ``python -m linuxbot -c config.yaml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from . import __version__
from .bot import Bot
from .config import ConfigError, load_config
from .logsetup import configure_logging
from .memory import DatabaseLocked, Memory


def _import_activity(config, paths: list[str], channel: str | None) -> int:
    """Riempie la mappa dell'attività con lo storico dei log (weechat o chatlog del bot)."""
    from collections import Counter

    from .activity import count_log, log_files
    from .irc import irc_lower

    if not config.memory.enabled:
        print("Errore: la mappa dell'attività vive nel database della memoria (memory.enabled: true).",
              file=sys.stderr)
        return 2
    files = log_files(paths)
    if not files:
        print("Nessun log da importare.", file=sys.stderr)
        return 2
    per_channel: dict[str, Counter] = {}
    for path in files:
        try:
            chan, counts = count_log(path, channel, {irc_lower(config.identity.nick)})
        except OSError as exc:
            print(f"Impossibile leggere {path}: {exc}", file=sys.stderr)
            return 2
        if not chan:
            print(f"{path}: non capisco il canale dal nome del file, indicalo con --channel", file=sys.stderr)
            return 2
        per_channel.setdefault(irc_lower(chan), Counter()).update(counts)
    try:
        mem = Memory(config.database_path, lock=True)
    except DatabaseLocked as exc:
        print(f"Errore: {exc}. Ferma il bot prima di importare.", file=sys.stderr)
        return 3
    try:
        for chan, counts in sorted(per_channel.items()):
            written = mem.activity_replace(chan, counts)
            print(f"{chan}: {written} messaggi in {len({d for d, _ in counts})} giorni")
    finally:
        mem.close()
    return 0


async def _main(bot: Bot) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(bot.run())

    def shutdown() -> None:
        bot.stop()
        loop.call_later(2, task.cancel)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:  # Windows
            pass
    if hasattr(signal, "SIGHUP"):
        def hup() -> None:
            try:
                bot.reload()
            except ConfigError as exc:
                logging.getLogger("linuxbot").error("Ricaricamento fallito: %s", exc)
        try:
            loop.add_signal_handler(signal.SIGHUP, hup)
        except NotImplementedError:
            pass
    try:
        await task
    except asyncio.CancelledError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linuxbot", description="Bot IRC configurabile via YAML")
    parser.add_argument("-c", "--config", default="config.yaml", help="file di configurazione (default: config.yaml)")
    parser.add_argument("--data-dir", default=os.environ.get("LINUXBOT_DATA_DIR") or None,
                        help="cartella per database, log e backup se indicati con percorsi relativi "
                             "(default: la cartella del file di configurazione; anche $LINUXBOT_DATA_DIR)")
    parser.add_argument("--check", action="store_true", help="valida la configurazione ed esce")
    parser.add_argument("--import-activity", nargs="+", metavar="LOG",
                        help="riempie la mappa dell'attività (!mappaattivita) coi log indicati ed esce: "
                             "log di weechat o file/cartelle della chatlog del bot; a bot fermo")
    parser.add_argument("--channel", help="con --import-activity: il canale dei log, se non si ricava dal nome")
    parser.add_argument("-v", "--verbose", action="store_true", help="log di debug (mostra il traffico IRC)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Errore di configurazione: {exc}", file=sys.stderr)
        return 2
    if args.data_dir:
        config.data_dir = Path(args.data_dir).expanduser().resolve()
        try:
            config.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"Impossibile creare la cartella dati {config.data_dir}: {exc}", file=sys.stderr)
            return 2

    if args.import_activity:
        return _import_activity(config, args.import_activity, args.channel)

    if args.check:
        def n(count: int, one: str, many: str) -> str:
            return f"{count} {one if count == 1 else many}"
        print(f"Configurazione valida: {n(len(config.commands), 'comando', 'comandi')}, "
              f"{n(len(config.responses), 'risposta', 'risposte')}, {n(len(config.channels), 'canale', 'canali')}.")
        if config.memory.enabled or config.bans.enabled or config.karma.enabled or config.schedules:
            print(f"Database: {config.database_path.expanduser().resolve()}")
        return 0

    try:
        configure_logging(config, verbose=args.verbose)  # subito, per vedere anche l'apertura del database
    except OSError as exc:
        print(f"Impossibile aprire il file di log: {exc}", file=sys.stderr)
        return 2
    try:
        bot = Bot(config)
    except DatabaseLocked as exc:
        print(f"Errore: {exc}. Chiudi l'altra copia o usa un altro database (memory.database).", file=sys.stderr)
        return 3
    configure_logging(config, bot, verbose=args.verbose)  # ora anche statistiche e avvisi su IRC
    # !reload e SIGHUP riapplicano anche la sezione logging
    bot.reload_hooks.append(lambda cfg: configure_logging(cfg, bot, verbose=args.verbose))
    logging.getLogger("linuxbot.core").info("The Linux Bot %s avviato con %s", __version__, config.path)
    asyncio.run(_main(bot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
