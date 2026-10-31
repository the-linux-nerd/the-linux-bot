"""Avvio da riga di comando: ``python -m linuxbot -c config.yaml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from . import __version__
from .bot import Bot
from .config import ConfigError, load_config


def setup_logging(level: str, logfile: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers,
    )


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
    parser.add_argument("--check", action="store_true", help="valida la configurazione ed esce")
    parser.add_argument("-v", "--verbose", action="store_true", help="log di debug (mostra il traffico IRC)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Errore di configurazione: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"Configurazione valida: {len(config.commands)} comandi, {len(config.responses)} risposte, "
              f"{len(config.channels)} canali.")
        return 0

    level = "DEBUG" if args.verbose else str(config.logging.get("level", "INFO"))
    setup_logging(level, config.logging.get("file"))
    asyncio.run(_main(Bot(config)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
