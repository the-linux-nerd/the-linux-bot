# The Linux Bot

Bot IRC per canali di sviluppatori, hacker e appassionati di Linux, scritto in Python (asyncio)
e configurabile interamente da un file YAML. Unica dipendenza: [PyYAML](https://pyyaml.org/);
richiede Python 3.10+.

## Documentazione

| | |
|---|---|
| **[Guida alla configurazione](docs/configurazione.md)** | dalla registrazione del nick al bot che parte all'accensione, passo passo, più cosa fare se qualcosa non va |
| **[Funzioni](docs/funzioni.md)** | come funziona ogni parte del bot, con esempi |
| **[Riferimento](docs/riferimento.md)** | tutte le opzioni di `config.yaml` con tipo e valore predefinito, le azioni, le variabili, i testi |
| [`config.minimal.yaml`](config.minimal.yaml) | configurazione minima da cui partire |
| [`config.example.yaml`](config.example.yaml) | configurazione d'esempio con tutto, commentata riga per riga |

## Cosa sa fare

- **Connessione** in chiaro o TLS, riconnessione automatica, anti-flood. **Identificazione
  automatica**: SASL se il server lo offre, altrimenti NickServ; riconosce Anope e Atheme da solo
  e si riprende il nick se è occupato. Può diventare IRC operator (`OPER`).
- **Op e canali**: chiede l'op a ChanServ e lo mantiene; imposta **topic, modi e descrizione**
  dei canali come scritto nel file; più canali, ognuno con le sue impostazioni; **crea e chiude
  canali** da IRC (`!crea`, `!chiudi`) registrandoli su ChanServ e ricordandoli.
- **Saluti** a chi entra, con regole per maschera (auto-voice, auto-op, niente saluto ai bot).
- **Comandi** con alias, argomenti, permessi (`anyone`, `voice`, `halfop`, `op`, `admin`) e
  cooldown; **risposte automatiche** a frasi scritte in canale (anche con regex).
- **Aiuto** completo: `!help`, `!help <comando>` con spiegazioni ed esempi, e il bot che si
  presenta ("TheLinuxBot: chi sei?") elencando le funzioni attive.
- **Link rapidi** per un canale tecnico: `!archwiki`, `!pkg`, `!tldr`, `!explainshell`, `!cve`,
  `!rfc`, `!xkcd`, `!man`...
- **Ricerca di risposte tecniche** su Stack Overflow (anche in italiano), unix.SE, Ask Ubuntu,
  ArchWiki e canali YouTube: `!cerca`, `?? domanda`, "TheLinuxBot: come faccio a ...?".
- **Memoria**: "quando è passato mario?", "chi è passato negli ultimi 3 giorni?", messaggi
  lasciati per chi non c'è e consegnati quando torna.
- **Karma** del canale (`nick++`, "grazie nick", `!karma`, `!top`) con regole anti-abuso,
  azzerato da un ban, e la top ten pubblicata ogni giorno a mezzanotte.
- **Ban**: ban a tempo, unban per nick anche dopo che l'utente è uscito, `!banlist`, `!baninfo`,
  ripristino dei ban tolti a mano, owner e admin immuni.
- **Attività programmate** a orari fissi o a intervalli, recuperate se il PC era spento.
- **Memoria permanente** in SQLite: karma, ban, messaggi e stato sopravvivono ai riavvii (anche a
  uno spegnimento improvviso), con backup automatico.
- **Log** per debug e ottimizzazione: livelli per area, rotazione, JSON, password sempre
  nascoste, avvisi agli admin su IRC, log delle chat, `!stats` con tempi e contatori.
- **Servizio systemd** installabile con un comando, che parte all'accensione.
- **Ricaricamento a caldo** della configurazione (`!reload` o `systemctl reload`).

## Avvio rapido

```bash
git clone https://github.com/the-linux-nerd/the-linux-bot && cd the-linux-bot
python3 -m venv .venv && .venv/bin/pip install -e .
cp config.minimal.yaml config.yaml                   # server, nick, canali, la tua maschera
export LINUXBOT_NICKSERV_PASSWORD='...'              # le password non vanno nel file
.venv/bin/python -m linuxbot -c config.yaml --check  # controlla la configurazione
.venv/bin/python -m linuxbot -c config.yaml -v       # prova (-v mostra il traffico IRC)
contrib/install-service.sh                           # poi: servizio che parte all'accensione
```

Prima di avviarlo il nick del bot va registrato con NickServ e gli va dato l'auto-op sui
canali: tutti i passi sono nella [guida](docs/configurazione.md).

## Sviluppo

```bash
pip install -e '.[test]'
python -m pytest
python contrib/gen-docs.py      # rigenera le parti di docs/riferimento.md che vengono dal codice
```

I test comprendono un finto server IRC locale che verifica l'intero ciclo (registrazione,
identificazione, recupero del nick, join, saluto, comandi, quit) e un controllo che la
documentazione elenchi esattamente le opzioni accettate dal bot.
