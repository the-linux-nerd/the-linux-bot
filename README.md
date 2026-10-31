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
- **Ban** con registro persistente: ban a tempo (`!tempban mario 2h flood`), unban per nick
  anche dopo che l'utente è uscito, `!banlist` e `!baninfo`, ripristino dei ban tolti a mano,
  espulsione di chi rientra, maschera configurabile.
- **Eventi**: azioni eseguite su connessione, ingresso del bot in un canale, kick, ecc.
- **Log** per debug e ottimizzazione: livelli per area, rotazione dei file, formato JSON,
  password sempre nascoste, avvisi agli admin su IRC, log delle chat per canale, `!stats` con
  tempi e contatori.
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
2. **Operatore del canale** (`+o`): lo dà ChanServ appena il bot entra. Servono tre cose:
   - **sui services**: registra il nick del bot e dagli l'auto-op nel canale
     (Anope: `/msg ChanServ AOP #linux ADD TheLinuxBot`; Atheme:
     `/msg ChanServ FLAGS #linux TheLinuxBot +Oo`);
   - **identificazione prima dell'ingresso**: con `auth.sasl` il bot è identificato già alla
     connessione; con `auth.nickserv` (e `wait_for_login: true`, il default) il bot aspetta la
     conferma di NickServ, o il numerico 900, prima di entrare nei canali, fino a `wait` secondi.
     Così ChanServ lo riconosce e gli dà l'op subito;
   - **`self_op`** come rete di sicurezza: dopo l'ingresso il bot controlla di essere op; se non
     lo è manda `OP #canale TheLinuxBot` a ChanServ (o `SAMODE` se è IRC operator e
     `method: auto`/`samode`), riprova ogni `retry` secondi fino a `max_retries` volte e lo
     richiede se qualcuno gli toglie l'op. Se non ci riesce lo scrive nel log, indicando di
     controllare la access list.

   ```yaml
   auth:
     sasl: { enabled: true, username: TheLinuxBot, password: ${LINUXBOT_SASL_PASSWORD} }
   self_op:
     enabled: true
     method: auto
     retry: 30
     max_retries: 3
   events:
     self_deop:
       - say: { to: "#linux-staff", text: "{nick} mi ha tolto l'op in {channel}" }
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

## Ban

Con `bans.enabled: true` ogni ban messo dal bot finisce in un registro (nello stesso database
della memoria) con chi l'ha messo, quando, perché e quando scade. Il bot deve essere op/halfop.

```
<carla> !tempban mario 2h flood in canale
  * mario è stato espulso da TheLinuxBot (flood in canale (bannato per 2 ore))
  -TheLinuxBot- Ban impostato su *!*@93-40-1-2.casa.it in #linux, scade fra 2 ore.
<carla> !banlist
  -TheLinuxBot- #1 *!*@93-40-1-2.casa.it (mario) da carla pochi secondi fa, scade fra 2 ore: flood in canale
  ... dopo 2 ore il bot toglie il ban da solo (anche se nel frattempo è stato riavviato)
<carla> !baninfo mario
  -TheLinuxBot- Nessun ban attivo per mario. L'ultimo (#1 ...) è stato tolto per scadenza 5 minuti fa.
```

| Comando | Cosa fa |
|---|---|
| `!ban <nick\|maschera> [motivo]` | ban permanente + kick (con una maschera espelle tutti quelli che corrispondono) |
| `!tempban <nick\|maschera> <durata> [motivo]` | ban a tempo: `30m`, `2h`, `1d`, `1w`, `1d12h`; un numero da solo sono minuti |
| `!unban <nick\|maschera\|#id>` | toglie il ban; col nick funziona anche se l'utente è uscito, usabile anche in privato |
| `!banlist [#canale]` | ban attivi (in privato indica il canale) |
| `!baninfo <nick\|maschera\|#id>` | dettagli, oppure l'ultimo ban già rimosso |

Comportamento:

- **Maschera** (`bans.mask`): `host` → `*!*@host` (default), `userhost` → `*!*ident@host`,
  `nick` → `nick!*@*`, `full`, oppure un modello come `*!*@{host}`. Se l'utente è uscito
  si usa l'ultimo host visto (serve `memory.enabled`). Si può cambiare per singolo comando
  con `mask_type:` nell'azione.
- **Enforcement** (`bans.enforce`): se un op toglie a mano (`/mode -b`) un ban messo dal bot,
  il bot lo rimette; chi è nel registro e riesce a rientrare viene espulso. Per togliere un
  ban si usa `!unban`. I ban messi a mano dagli op vengono registrati (`track_manual`) e
  si possono togliere anche a mano.
- **Sincronizzazione**: quando entra in un canale il bot legge la lista ban del server,
  importa quelli che non conosce e rimette quelli del registro spariti (appena ha l'op).
- **Immuni**: il bot non banna né espelle mai sé stesso, gli admin del bot, gli owner del
  canale (`channels[].owners`) e chi è in `bans.immune` (validi in tutti i canali). Vale anche
  per le maschere larghe (`!ban *!*@*.it` viene rifiutato se colpisce un owner) e per
  l'espulsione automatica. Se un op banna a mano un immune, il bot toglie il ban
  (`bans.unban_immune`), anche quando lo trova nella lista ban del server entrando nel canale.

  ```yaml
  channels:
    - name: "#linux"
      owners: ["*!*@the-linux-nerd.example.org", "*!*@user/fabio"]
  bans:
    immune: ["*!*@*.staff.example.org"]
  ```

  Usa maschere sull'host o sul cloak dei services: una maschera sul solo nick (`fabio!*@*`)
  la soddisfa chiunque prenda quel nick.
- `default_duration` rende a tempo anche i `!ban` senza durata; `max_duration` vieta ban più
  lunghi (e quelli permanenti).

Testi personalizzabili in `bans.texts`: `kick_reason`, `kick_reason_temp`, `default_reason`,
`ban_done`, `unban_done`, `unban_unknown`, `bad_duration`, `too_long`, `no_channel`,
`list_header`, `list_item`, `list_more`, `list_empty`, `info`, `info_removed`, `info_none`,
`expiry_permanent`, `expiry_in`, `enforce_reason`, `immune`.

## Struttura della configurazione

Il file [`config.example.yaml`](config.example.yaml) è commentato riga per riga e contiene
un esempio di tutto. In sintesi:

| Sezione      | Cosa contiene |
|--------------|---------------|
| `server`     | host, porta, TLS, password, encoding |
| `identity`   | nick, nick alternativi, username, realname, risposta CTCP VERSION |
| `auth`       | `sasl`, `nickserv`, `oper`, `user_modes` |
| `self_op`    | come il bot ottiene e mantiene l'op nei canali |
| `channels`   | canali (nome o `{name, key, owners}`) |
| `admins`     | maschere `nick!user@host` degli amministratori del bot |
| `behavior`   | prefissi dei comandi, utenti ignorati, anti-flood, riconnessione, messaggi di sistema |
| `variables`  | variabili personalizzate usabili in tutti i testi |
| `greetings`  | saluti a chi entra |
| `responses`  | risposte automatiche |
| `commands`   | comandi |
| `events`     | azioni su eventi IRC |
| `memory`     | database, "seen", messaggi lasciati e relativi testi |
| `bans`       | registro dei ban, maschera, scadenze, enforcement e relativi testi |
| `logging`    | log tecnici (livelli per area, rotazione, JSON, avvisi su IRC) e log delle chat |

### Testi e variabili

In ogni testo puoi usare `{nick}`, `{channel}`, `{botnick}`, `{arg1}`, `{args}`, `{rest}`,
`{rest2}` (dal 3° argomento in poi), `{target}`, `{date}`, `{time}`, i gruppi delle regex e le tue `variables` (l'elenco completo
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
| `ban` | `ban: "{arg1}"` (nick → `*!*@host`) | `ban: {channel, nick\|mask, duration, reason, mask_type, confirm}` |
| `kickban` | `kickban: "{arg1}"` | come `ban` |
| `unban` | `unban: "{arg1}"` (nick, maschera o `#id`) | `unban: {channel, nick\|mask}` |
| `banlist` | `banlist` | `banlist: {channel}` |
| `baninfo` | `baninfo: "{arg1}"` | `baninfo: {nick\|mask, channel}` |
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

`connect`, `identified`, `self_join`, `self_op`, `self_deop`, `self_kick`, `join`, `part`,
`quit`, `nick`, `kick`, `topic`, `invite`. Variabili extra: `{reason}`, `{victim}`, `{kicker}`, `{old_nick}`, `{new_nick}`, `{topic}`.

## Log, debug e statistiche

Tutto si configura nella sezione `logging` e si ricarica con `!reload` (o `SIGHUP`) senza
riavviare il bot. `-v` da riga di comando forza il livello DEBUG ovunque.

- **Aree**: ogni riga di log indica l'area da cui viene:
  `2026-10-31 19:08:11 INFO    [op] Chiedo l'op in #linux (tentativo 1 di 2)`.
  Le aree sono `connection`, `irc` (traffico grezzo, a DEBUG), `auth`, `channels`, `commands`,
  `greetings`, `memory`, `bans`, `op`, `events`, `config`, `stats` (più `core` per il resto).
  Con `levels` si fa debug mirato senza riempire il log:

  ```yaml
  logging:
    level: INFO
    levels: { irc: DEBUG, bans: DEBUG, memory: WARNING }
  ```

- **File con rotazione**: `file.rotate: size` (con `max_bytes`, es. `10MB`) oppure `daily`
  (a mezzanotte), tenendo `backups` file vecchi. I percorsi relativi partono dalla cartella
  della configurazione.
- **JSON** (`format: json`): una riga per evento con `ts`, `level`, `area`, `msg` ed eventuale
  `exc`, pronta per journald, Loki, ELK o `jq`.
- **Password nascoste**: le password e le chiavi presenti nella configurazione vengono
  sostituite con `********` ovunque compaiano nei log, anche se mandate con `raw`; in più
  `PASS`, `OPER`, `AUTHENTICATE` e `IDENTIFY` ai services. Con `redact` aggiungi altre
  espressioni regolari (con un gruppo `()` si nasconde solo quello).
- **Lentezze**: righe e azioni che superano `slow_threshold` secondi, e operazioni sul
  database oltre `slow_db_threshold`, generano un avviso con il nome dell'operazione e la durata.
- **Avvisi su IRC** (`alerts`): gli eventi dal livello scelto (default `ERROR`) arrivano in
  notice a canali o nick, ad esempio
  `[ERRORE] op: Non ho ottenuto l'op in #linux: controlla che il bot sia nella access list di ChanServ`.
  Lo stesso messaggio non viene ripetuto prima di `min_interval` secondi e in tutto non più di
  `max_per_hour` avvisi l'ora.
- **Log delle chat** (`chatlog`): un file al giorno per canale in
  `logs/chat/<canale>/AAAA-MM-GG.log`, con messaggi (anche quelli del bot), azioni `/me`,
  ingressi, uscite, kick, cambi di nick, topic e modi; opzionalmente i privati in
  `logs/chat/_privati/`. I file più vecchi di `retention_days` vengono cancellati.

  ```
  [19:08:11] --> anna (a@host) è entrato in #linux
  [19:08:11] <anna> !ping
  [19:08:11] <TheLinuxBot> pong
  ```

- **`!stats`** (solo admin; `!stats generale|comandi|flood|db|errori` per una sezione sola):

  ```
  Attivo da 3 giorni e 2 ore, connesso da 5 ore a irc.example.org (connessioni: 2, cadute: 1, ultima: il server non risponde). Righe ricevute 18234 (1520 KB), inviate 912 (61 KB).
  Comandi: 140 (help 41, seen 30, ...); tempo medio 0.2 ms, massimo 12 ms (!banlist). Risposte automatiche: 35 (saluto 20, ...).
  Elaborazione righe: media 0.1 ms, massimo 9 ms, lente 0. Azioni: 170, media 0.1 ms, massimo 11 ms (banlist !banlist), lente 0.
  Anti-flood: 15 messaggi ritardati, attesa totale 12.4 s, coda massima 6 (ora 0).
  Database: 4210 operazioni, media 0.3 ms, massimo 18 ms (recent), lente 0.
  Log: 1 errori, 4 avvisi. Ultimo errore (30/10 21:14, connection): Connessione persa: ...
  ```

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
