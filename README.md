# The Linux Bot

Bot IRC scritto in Python (asyncio) e configurabile interamente da un file YAML:
connessione, autenticazione, saluti, risposte automatiche, comandi e azioni.

- **Connessione** in chiaro o TLS, password del server, nick alternativi, riconnessione
  automatica con backoff, keepalive, protezione anti-flood.
- **Autenticazione** con SASL PLAIN, NickServ e/o `OPER` (per diventare IRC operator),
  più modi utente (es. `+B`). Le password possono venire da variabili d'ambiente.
- **Saluti** a chi entra, con messaggi casuali, ritardo, cooldown per utente e regole per
  maschera (es. auto-voice/auto-op per host fidati, nessun saluto per i services).
- **Risposte automatiche** a frasi scritte in canale (`contains`, `word`, `exact`,
  `startswith`, `regex` con gruppi), con cooldown, probabilità e risposta solo se citato.
- **Comandi** (`!help`, `!regole`, `!kick`...) con alias, argomenti, permessi
  (`anyone`, `voice`, `halfop`, `op`, `admin`), cooldown, help generato in automatico.
- **Azioni** componibili: `say`, `notice`, `me`, `kick`, `ban`, `kickban`, `op`, `voice`,
  `topic`, `mode`, `join`, `part`, `invite`, `raw`, `delay`, `reload`, `quit`...
- **Memoria** (SQLite): ricorda chi passa e risponde a "quando è passato mario?" e
  "chi è passato negli ultimi 3 giorni?"; si possono **lasciare messaggi** agli utenti, che il
  bot consegna quando entrano, quando scrivono o quando chiedono "ci sono messaggi per me?".
- **Eventi**: azioni eseguite su connessione, ingresso del bot in un canale, kick, ecc.
- **Ricaricamento a caldo** della configurazione (comando `!reload` o `SIGHUP`).

Unica dipendenza: [PyYAML](https://pyyaml.org/). Richiede Python 3.10+.

## Avvio rapido

```bash
python3 -m venv .venv
.venv/bin/pip install -e .           # oppure: pip install -r requirements.txt
cp config.example.yaml config.yaml   # poi modificalo
.venv/bin/python -m linuxbot -c config.yaml --check   # valida la configurazione
.venv/bin/python -m linuxbot -c config.yaml           # avvia (-v per vedere il traffico IRC)
```

Le password è meglio non scriverle nel file: in `config.yaml` usa `${LINUXBOT_OPER_PASSWORD}`
e definisci la variabile d'ambiente (vedi `contrib/linuxbot.service` per systemd).
`config.yaml` è già in `.gitignore`.

## Diventare operatore

Ci sono due significati di "operatore", e il bot li gestisce entrambi:

1. **IRC operator del server** (`OPER`): nel server (es. `ircd.conf` di UnrealIRCd/InspIRCd)
   va definito un blocco oper per il bot; poi in `auth.oper` metti nome e password.
   Il bot invia `OPER nome password` appena registrato.
2. **Operatore del canale** (`+o`): di solito lo dà ChanServ. Registra il nick del bot,
   aggiungilo alla access list del canale con auto-op, abilita `auth.nickserv` (o `auth.sasl`)
   e, se vuoi, chiedi l'op esplicitamente all'ingresso:

   ```yaml
   events:
     self_join:
       - say: { to: ChanServ, text: "OP {channel} {botnick}" }
       # oppure, da IRC operator:
       # - raw: "SAMODE {channel} +o {botnick}"
   ```

## Il saluto e la "@"

Il saluto scrive il nick così com'è (`Ciao mario, benvenuto…`): i client IRC lo evidenziano
già. Se preferisci lo stile `@mario` scrivi `@{nick}` nel testo. La `@` che compare davanti al
nick nella lista utenti è invece lo stato di operatore: la dà l'azione `op`, che nell'esempio
viene usata solo per gli host fidati (`greetings.rules`).

## Memoria: "seen", chi è passato, messaggi lasciati

Con `memory.enabled: true` il bot salva in un database SQLite (`memory.database`, relativo alla
cartella del file di configurazione) l'ultima attività di ogni utente per canale: ingresso,
uscita, disconnessione, kick, cambio nick, messaggi. Gli utenti in `behavior.ignore` non vengono
registrati; i dati più vecchi di `retention_days` vengono cancellati.

```
<fabio> !seen mario                       (oppure: quando è passato mario?)
<bot>   mario è passato 2 giorni fa (29/10/2026 21:14) in #linux, mentre si disconnetteva (Ping timeout).
<fabio> chi è passato negli ultimi 3 giorni?      (oppure: !passati 3)
<bot>   Passati negli ultimi 3 giorni (2): fabio (pochi secondi fa), mario (2 giorni fa)
<fabio> TheLinuxBot: lascia un messaggio a mario: domani si aggiorna il kernel
                                                  (oppure: !lascia mario domani si aggiorna il kernel)
<bot>   Ok fabio, riferirò il messaggio a mario appena lo vedo.
  ... mario entra, scrive qualcosa, oppure chiede "ci sono messaggi per me?" / !messaggi ...
<bot>   (in privato a mario) mario, fabio ti ha lasciato un messaggio 3 ore fa: domani si aggiorna il kernel
```

I messaggi sono legati al **nick** (non all'account): li riceve chiunque usi quel nick.
Scadono dopo `memos.expire_days` e ogni utente può averne in attesa al massimo `memos.max_pending`.

Le azioni disponibili sono `seen`, `recent`, `memo_add`, `memo_read` (vedi tabella sotto), quindi
puoi collegarle a qualsiasi comando o frase. Tutti i testi si cambiano in `memory.texts`; le chiavi sono:
`seen`, `seen_here`, `seen_never`, `seen_self`, `seen_bot`, `seen_usage`, `event_join`, `event_part`,
`event_quit`, `event_kick`, `event_nick`, `event_nick_from`, `event_message`, `event_message_text`,
`recent`, `recent_none`, `recent_more`, `memo_saved`, `memo_usage`, `memo_self`, `memo_bot`,
`memo_too_long`, `memo_full`, `memo_delivered`, `memo_none`.

## Struttura della configurazione

Il file [`config.example.yaml`](config.example.yaml) è commentato riga per riga e contiene
un esempio di tutto. In sintesi:

| Sezione      | Cosa contiene |
|--------------|---------------|
| `server`     | host, porta, TLS, password, encoding |
| `identity`   | nick, nick alternativi, username, realname, risposta CTCP VERSION |
| `auth`       | `sasl`, `nickserv`, `oper`, `user_modes` |
| `channels`   | canali (nome o `{name, key}`) |
| `admins`     | maschere `nick!user@host` degli amministratori del bot |
| `behavior`   | prefissi dei comandi, utenti ignorati, anti-flood, riconnessione, messaggi di sistema |
| `variables`  | variabili personalizzate usabili in tutti i testi |
| `greetings`  | saluti a chi entra |
| `responses`  | risposte automatiche |
| `commands`   | comandi |
| `events`     | azioni su eventi IRC |
| `memory`     | database, "seen", messaggi lasciati e relativi testi |
| `logging`    | livello e file di log |

### Testi e variabili

In ogni testo puoi usare `{nick}`, `{channel}`, `{botnick}`, `{arg1}`, `{args}`, `{rest}`,
`{target}`, `{date}`, `{time}`, i gruppi delle regex e le tue `variables` (l'elenco completo
è in testa a `config.example.yaml`). Se al posto di un testo metti una lista, il bot ne
sceglie uno a caso. Un testo su più righe (`|` in YAML) diventa più messaggi.

### Un comando

```yaml
commands:
  kick:
    aliases: [k]
    description: "Espelle un utente"
    usage: "<nick> [motivo]"
    permission: op          # anyone | voice | halfop | op | admin
    min_args: 1
    private: false          # non usabile in query
    cooldown: 0
    actions:
      - kick: { nick: "{arg1}", reason: "{rest}" }

  sito: "Il nostro sito: {site}"   # forma breve: solo risposta
```

Il bot risponde a `!kick`, ai prefissi alternativi (`.kick`) e quando viene chiamato per
nome (`TheLinuxBot: kick pippo`). Nella forma senza prefisso hanno la precedenza le risposte
automatiche: "TheLinuxBot: lascia un messaggio a…" usa la frase, non il comando `!lascia`. Gli admin non sono soggetti a cooldown.
Il permesso `op`/`voice` si verifica sullo stato dell'utente nel canale; in query privata
vale lo stato in un qualsiasi canale in comune.

### Una risposta automatica

```yaml
responses:
  - name: distro
    match: regex
    patterns: ['(che|quale) distro (mi )?consigli']
    reply: "{nick}: Mint, Fedora o Debian vanno benissimo!"
    cooldown: 120           # per canale
    addressed: false        # true = solo se il bot viene citato
```

### Azioni

Ogni azione è una mappa con una sola chiave. La forma breve è una stringa; quella estesa
una mappa. Il canale di default è quello in cui è arrivato il comando.

| Azione | Forma breve | Forma estesa |
|---|---|---|
| `say` / `reply` / `msg` | `say: "testo"` | `say: {to: "#canale", text: "..."}` |
| `notice` | `notice: "testo"` (all'utente) | `notice: {to: nick, text: "..."}` |
| `me` | `me: "balla"` | `me: {to: "#canale", text: "..."}` |
| `kick` | `kick: "{arg1}"` | `kick: {channel, nick, reason}` |
| `ban` / `unban` | `ban: "{arg1}"` (nick → `*!*@host`) | `ban: {channel, mask}` o `{channel, nick}` |
| `kickban` | `kickban: "{arg1}"` | `kickban: {channel, nick, reason}` |
| `op` `deop` `halfop` `dehalfop` `voice` `devoice` | `op: "{nick}"` | `op: {channel, nick}` |
| `mode` | `mode: "#canale +m"` | `mode: {target, modes}` |
| `topic` | `topic: "{args}"` | `topic: {channel, text}` |
| `invite` | `invite: "{arg1}"` | `invite: {channel, nick}` |
| `join` / `part` | `join: "#canale [chiave]"` | `join: {channel, key}` / `part: {channel, reason}` |
| `nick` | `nick: "NuovoNick"` | |
| `raw` | `raw: "PRIVMSG X :ciao"` | |
| `delay` | `delay: 2` (secondi) | |
| `help` | `help` / `help: say` | `help: {method: notice}` |
| `reload` | `reload` | `reload: {ok: "...", error: "... {error}"}` |
| `quit` | `quit: "motivo"` | |
| `seen` | `seen: "{arg1}"` | `seen: {nick}` |
| `recent` | `recent: "{arg1}"` (giorni) | `recent: {days, hours, channel, all_channels}` |
| `memo_add` | | `memo_add: {to: "{arg1}", text: "{rest}"}` |
| `memo_read` | `memo_read` | |

### Eventi

`connect`, `self_join`, `self_kick`, `join`, `part`, `quit`, `nick`, `kick`, `topic`,
`invite`. Variabili extra: `{reason}`, `{victim}`, `{kicker}`, `{old_nick}`, `{new_nick}`, `{topic}`.

## Esecuzione come servizio

Vedi [`contrib/linuxbot.service`](contrib/linuxbot.service): `systemctl reload linuxbot`
invia `SIGHUP` e ricarica la configurazione senza disconnettere il bot.
Le modifiche a `server` e `identity` richiedono un riavvio.

## Sviluppo

```bash
pip install -e '.[test]'
python -m pytest
```

I test comprendono un finto server IRC locale che verifica l'intero ciclo
(registrazione, OPER, join, saluto, comandi, quit).
