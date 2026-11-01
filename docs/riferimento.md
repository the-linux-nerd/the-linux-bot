# Riferimento della configurazione

Tutte le opzioni di `config.yaml`, sezione per sezione. Per iniziare da zero segui la
[guida alla configurazione](configurazione.md); per le spiegazioni delle funzioni vedi
[funzioni.md](funzioni.md).

Convenzioni:

- **testo**: una stringa. Dove è indicato *(lista = a caso)* puoi mettere una lista e il bot ne
  sceglie una a caso ogni volta. Nei testi si usano le [variabili](#variabili-nei-testi).
- **durata**: un numero di secondi, oppure (dove indicato) un testo come `30m`, `2h`, `1d`,
  `1w`, `1d12h` (m minuti, h ore, d giorni, w settimane; un numero da solo sono minuti).
- **maschera**: `nick!user@host` con `*` e `?` come jolly, es. `*!*@*.example.org`.
- **percorso**: se è relativo parte dalla cartella dati (`--data-dir`, usata dal servizio) o,
  senza, dalla cartella del file di configurazione.
- Ogni testo può contenere `${VARIABILE}` o `${VARIABILE:-predefinito}`, sostituiti con le
  variabili d'ambiente all'avvio (per le password).
- Una chiave sconosciuta è un errore: `python -m linuxbot -c config.yaml --check` dice quale.

## Indice

[server](#server) · [identity](#identity) · [auth](#auth) · [channels](#channels) ·
[channel_management e channel_templates](#channel_management-e-channel_templates) ·
[admins](#admins) · [behavior](#behavior) · [variables](#variables) ·
[greetings](#greetings) · [responses](#responses) · [commands](#commands) ·
[events](#events) · [memory](#memory) · [bans](#bans) · [self_op](#self_op) ·
[about](#about) · [search](#search) · [karma](#karma) · [schedules](#schedules) ·
[logging](#logging) · [azioni](#azioni) · [variabili](#variabili-nei-testi) ·
[testi predefiniti](#testi-predefiniti)

## server

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `server.host` | testo | **obbligatorio** | indirizzo del server IRC |
| `server.port` | numero | 6697 con TLS, 6667 senza | porta |
| `server.tls` | sì/no | `false` | connessione cifrata |
| `server.tls_verify` | sì/no | `true` | verifica il certificato (metti `false` solo per certificati self-signed) |
| `server.password` | testo | nessuna | password del server (comando `PASS`), se serve |
| `server.encoding` | testo | `utf-8` | codifica dei messaggi; le righe non valide vengono lette come latin-1 |

## identity

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `identity.nick` | testo | **obbligatorio** | nick del bot |
| `identity.alt_nicks` | lista | `[]` | nick di riserva se il principale è occupato (poi `nick1`, `nick2`...) |
| `identity.username` | testo | `linuxbot` | username (la parte `user` di `nick!user@host`) |
| `identity.realname` | testo | `The Linux Bot` | nome mostrato nel `/whois` |
| `identity.version` | testo | `The Linux Bot` | risposta al CTCP VERSION |

## auth

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `auth.sasl.enabled` | `auto` / `true` / `false` | `auto` | SASL PLAIN: `auto` lo usa se il server lo offre e c'è una password |
| `auth.sasl.username` | testo | `auth.nickserv.account` o `identity.nick` | account SASL |
| `auth.sasl.password` | testo | la password di NickServ | password SASL |
| `auth.nickserv.enabled` | sì/no | `false` | identificazione con NickServ |
| `auth.nickserv.password` | testo | — (obbligatoria se abilitato) | password dell'account |
| `auth.nickserv.account` | testo | `identity.nick` | account registrato |
| `auth.nickserv.service` | testo | `NickServ` | nick di NickServ |
| `auth.nickserv.command` | testo | `auto` | `auto` = `IDENTIFY {password}` o, da un nick alternativo, `IDENTIFY {account} {password}`; oppure un comando a piacere |
| `auth.nickserv.services` | testo | `auto` | `auto` (lo chiede a NickServ), `anope`, `atheme`, `other` |
| `auth.nickserv.regain` | sì/no | `true` | se il nick principale è occupato se lo riprende (REGAIN/RECOVER/GHOST) |
| `auth.nickserv.regain_commands` | lista | automatico | comandi da provare in ordine, es. `["GHOST {nick} {password}"]` |
| `auth.nickserv.wait_for_login` | sì/no | `true` | entra nei canali solo dopo la conferma di NickServ |
| `auth.nickserv.wait` | secondi | `15` | attesa massima della conferma (0 = non aspettare) |
| `auth.nickserv.success_pattern` | regex | frasi di Anope/Atheme in inglese e italiano | riconosce la conferma |
| `auth.nickserv.failure_pattern` | regex | frasi di password errata | riconosce il rifiuto |
| `auth.oper.enabled` | sì/no | `false` | diventa IRC operator con `OPER` |
| `auth.oper.name` | testo | `identity.nick` | nome del blocco oper |
| `auth.oper.password` | testo | — (obbligatoria se abilitato) | password oper |
| `auth.user_modes` | testo | nessuno | modi utente dopo la connessione, es. `+B` |

## channels

Lista dei canali. Un elemento può essere solo il nome (`"#linux"`) o una mappa:

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `channels[].name` | testo | **obbligatorio** | nome del canale (`#...`) |
| `channels[].key` | testo | nessuna | chiave per entrare (`+k`) |
| `channels[].owners` | lista di maschere | `[]` | owner del canale: immuni a ban e kick |
| `channels[].topic` | testo | nessuno | topic da garantire (impostato se diverso) |
| `channels[].topic_lock` | sì/no | `false` | rimette il topic se qualcuno lo cambia |
| `channels[].modes` | testo | nessuno | modi da garantire, sintassi IRC: `+nt`, `+ntC-s`, `+ntl 80` |
| `channels[].modes_lock` | sì/no | `false` | rimette i modi se qualcuno li cambia |
| `channels[].description` | testo | nessuna | descrizione presso ChanServ (Anope: `SET DESC`) |
| `channels[].description_command` | testo | automatico | comando per ChanServ, es. `SET DESC {channel} {description}` |

## channel_management e channel_templates

Canali creati con `!crea` (azione `channel_create`): stanno nel database e si aggiungono a
`channels` a ogni avvio.

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `channel_management.default_template` | testo | nessuno | modello usato se `!crea` non ne indica uno |
| `channel_management.register` | sì/no | `true` | registra su ChanServ i canali creati |
| `channel_management.register_command` | testo | `REGISTER {channel}` | comando di registrazione |
| `channel_management.drop` | sì/no | `true` | `!chiudi` toglie la registrazione |
| `channel_management.drop_command` | testo | `DROP {channel}` | comando di cancellazione (il codice di conferma di Atheme viene gestito da solo) |
| `channel_management.confirm_seconds` | secondi | `60` | tempo per confermare `!chiudi` |
| `channel_management.max_channels` | numero | `20` | canali creati al massimo |
| `channel_templates.<nome>` | mappa | — | modello: stesse chiavi di `channels[]` tranne `name` |

## admins

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `admins` | lista di maschere | `[]` | amministratori del bot: permesso `admin`, niente cooldown, immuni |

## behavior

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `behavior.command_prefixes` | lista | `["!"]` | prefissi dei comandi |
| `behavior.command_prefix` | testo | — | forma breve di `command_prefixes` con un solo prefisso |
| `behavior.answer_when_addressed` | sì/no | `true` | "TheLinuxBot: help" vale come `!help` |
| `behavior.respond_to_private` | sì/no | `true` | risponde ai messaggi privati |
| `behavior.ignore` | lista di maschere | `[]` | utenti ignorati (non salutati, non registrati in memoria) |
| `behavior.rejoin_on_kick` | sì/no | `true` | rientra se viene espulso |
| `behavior.rejoin_delay` | secondi | `5` | attesa prima di rientrare |
| `behavior.reconnect_delay` | secondi | `10` | attesa prima di riconnettersi (raddoppia a ogni tentativo) |
| `behavior.reconnect_max_delay` | secondi | `300` | attesa massima fra i tentativi |
| `behavior.flood_burst` | numero | `4` | messaggi inviabili subito |
| `behavior.flood_rate` | secondi | `1.0` | poi uno ogni N secondi |
| `behavior.max_line_bytes` | numero | `400` | i messaggi più lunghi vengono spezzati |
| `behavior.unknown_command_reply` | testo | nessuna | risposta a un comando sconosciuto |
| `behavior.permission_denied_reply` | testo | `{nick}: non hai i permessi per usare {command}.` | risposta se mancano i permessi |
| `behavior.cooldown_reply` | testo | nessuna | risposta se il comando è in cooldown |
| `behavior.timezone` | testo | quello del sistema | fuso orario (es. `Europe/Rome`) per attività, `{date}`, `{time}` |

## variables

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `variables` | mappa | `{}` | variabili tue, usabili in tutti i testi: `site: "https://..."` → `{site}` |

## greetings

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `greetings.enabled` | sì/no | `false` | saluta chi entra |
| `greetings.channels` | lista | tutti | canali in cui salutare |
| `greetings.delay` | secondi | `1` | attesa prima del saluto (se esce prima, niente saluto) |
| `greetings.cooldown` | secondi | `3600` | non risalutare la stessa persona prima di così |
| `greetings.method` | `say` / `notice` | `say` | nel canale o in notice alla persona |
| `greetings.messages` | lista di testi | `[]` | saluti, uno a caso |
| `greetings.actions` | lista di [azioni](#azioni) | `[]` | eseguite dopo ogni saluto |
| `greetings.rules` | lista | `[]` | regole per maschera, vedi sotto |
| `greetings.rules[].match` | maschera o lista | **obbligatorio** | a chi si applica |
| `greetings.rules[].channels` | lista | tutti | in quali canali |
| `greetings.rules[].messages` | lista di testi | quelli generali | saluti al posto di quelli generali |
| `greetings.rules[].method` | `say` / `notice` | quello generale | come salutare |
| `greetings.rules[].actions` | lista di azioni | `[]` | azioni in più (es. `voice`, `op`) |
| `greetings.rules[].skip` | sì/no | `false` | non salutare |

## responses

Risposte automatiche a ciò che si scrive nei canali. Lista di:

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `responses[].name` | testo | `responseN` | nome (log, statistiche, cooldown) |
| `responses[].patterns` | lista | **obbligatorio** (o `pattern`) | cosa cercare |
| `responses[].pattern` | testo | — | forma breve con un solo pattern |
| `responses[].match` | testo | `contains` | `contains`, `word`, `exact`, `startswith`, `regex` |
| `responses[].case_sensitive` | sì/no | `false` | distingue maiuscole e minuscole |
| `responses[].reply` | testo *(lista = a caso)* | — | risposta (serve `reply` o `actions`) |
| `responses[].method` | `say` / `notice` | `say` | come rispondere |
| `responses[].actions` | lista di azioni | `[]` | azioni da eseguire |
| `responses[].channels` | lista | tutti | dove vale |
| `responses[].private` | sì/no | `true` | vale anche in privato |
| `responses[].addressed` | sì/no | `false` | solo se il messaggio cita il bot (o è privato) |
| `responses[].cooldown` | secondi | `30` | pausa fra due risposte nello stesso canale |
| `responses[].probability` | da 0 a 1 | `1` | probabilità di rispondere |
| `responses[].permission` | permesso | `anyone` | chi la può far scattare |
| `responses[].stop` | sì/no | `true` | dopo questa non controlla le successive |
| `responses[].description` | testo | nessuna | compare in "cosa so fare" del bot |

Con `match: regex` i gruppi sono disponibili come `{group1}`, `{group2}`... e quelli con nome
(`(?P<chi>...)`) come `{chi}`; `{match}` è tutto il testo trovato.

## commands

Mappa nome → comando. Forma breve: `sito: "Il nostro sito: {site}"`.

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `commands.<nome>.aliases` | lista | `[]` | altri nomi |
| `commands.<nome>.description` | testo | nessuna | descrizione (help) |
| `commands.<nome>.usage` | testo | nessuno | argomenti, es. `<nick> [motivo]` |
| `commands.<nome>.details` | lista di testi | `[]` | spiegazione estesa per `!help <comando>` |
| `commands.<nome>.examples` | lista di testi | `[]` | esempi d'uso |
| `commands.<nome>.permission` | permesso | `anyone` | `anyone`, `voice`, `halfop`, `op`, `admin` |
| `commands.<nome>.min_args` | numero | `0` | argomenti minimi, altrimenti "Uso: ..." |
| `commands.<nome>.args_pattern` | regex | nessuna | gli argomenti devono rispettarla, altrimenti "Uso: ..." |
| `commands.<nome>.reply` | testo *(lista = a caso)* | — | risposta (serve `reply` o `actions`) |
| `commands.<nome>.method` | `say` / `notice` | `say` | come rispondere |
| `commands.<nome>.actions` | lista di azioni | `[]` | azioni da eseguire |
| `commands.<nome>.channels` | lista | tutti | dove funziona |
| `commands.<nome>.private` | sì/no | `true` | funziona anche in privato |
| `commands.<nome>.cooldown` | secondi | `0` | pausa fra due usi nello stesso canale (gli admin non ce l'hanno) |
| `commands.<nome>.hidden` | sì/no | `false` | non compare nell'help |
| `commands.<nome>.enabled` | sì/no | `true` | per disattivarlo senza cancellarlo |

Permessi: `voice`, `halfop` e `op` si verificano sullo stato dell'utente nel canale (in privato,
in qualsiasi canale in comune); `admin` sulle maschere di `admins`.

## events

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `events.<evento>` | lista di azioni | — | azioni eseguite quando succede l'evento |

Eventi: `connect`, `identified`, `self_join`, `self_op`, `self_deop`, `self_kick`, `join`,
`part`, `quit`, `nick`, `kick`, `topic`, `invite`. Variabili in più: `{reason}`, `{victim}`,
`{kicker}`, `{old_nick}`, `{new_nick}`, `{topic}`.

## memory

Database SQLite con chi passa, i messaggi lasciati (e, se attivi, karma, ban, canali creati,
stato delle attività).

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `memory.enabled` | sì/no | `false` | "seen", chi è passato, messaggi lasciati |
| `memory.database` | percorso | `linuxbot.db` | file del database |
| `memory.retention_days` | giorni | `365` | dimentica chi non si vede da più di così |
| `memory.store_messages` | sì/no | `false` | "seen" riporta anche l'ultima frase scritta |
| `memory.default_days` | giorni | `7` | per "chi è passato" senza numero |
| `memory.max_names` | numero | `25` | nick elencati al massimo |
| `memory.memos.deliver_on_join` | sì/no | `true` | consegna i messaggi quando il destinatario entra |
| `memory.memos.deliver_on_message` | sì/no | `true` | ...e quando scrive |
| `memory.memos.method` | testo | `private` | `private`, `notice`, `say` |
| `memory.memos.delay` | secondi | `3` | attesa dopo l'ingresso |
| `memory.memos.max_length` | numero | `300` | lunghezza massima di un messaggio |
| `memory.memos.max_pending` | numero | `10` | messaggi in attesa per destinatario |
| `memory.memos.expire_days` | giorni | `60` | i messaggi non consegnati scadono |
| `memory.texts` | mappa | vedi [testi](#testi-predefiniti) | testi di seen, passati e messaggi |

## bans

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `bans.enabled` | sì/no | `false` | registro dei ban, ban a tempo, banlist, enforcement |
| `bans.mask` | testo | `host` | maschera per i nick: `host`, `userhost`, `nick`, `full` o un modello con `{nick}` `{ident}` `{user}` `{host}` |
| `bans.enforce` | sì/no | `true` | rimette i ban tolti a mano ed espelle chi rientra |
| `bans.track_manual` | sì/no | `true` | registra anche i ban messi a mano |
| `bans.check_interval` | secondi | `30` | controllo dei ban scaduti |
| `bans.default_duration` | durata (testo) | permanente | durata dei ban senza durata |
| `bans.max_duration` | durata (testo) | nessun limite | durata massima (vieta anche i permanenti) |
| `bans.list_max` | numero | `10` | righe di `!banlist` |
| `bans.immune` | lista di maschere | `[]` | immuni a ban e kick in tutti i canali |
| `bans.unban_immune` | sì/no | `true` | toglie i ban messi da altri che colpiscono un immune |
| `bans.texts` | mappa | vedi [testi](#testi-predefiniti) | testi |

## self_op

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `self_op.enabled` | sì/no | `false` | il bot controlla di essere op e lo chiede |
| `self_op.method` | testo | `chanserv` | `chanserv`, `samode`, `auto` (SAMODE se è IRC operator) |
| `self_op.service` | testo | `ChanServ` | nick di ChanServ |
| `self_op.command` | testo | `OP {channel} {botnick}` | richiesta a ChanServ |
| `self_op.samode` | testo | `SAMODE {channel} +o {botnick}` | comando da IRC operator |
| `self_op.delay` | secondi | `2` | attesa dopo l'ingresso |
| `self_op.retry` | secondi | `30` | attesa fra i tentativi |
| `self_op.max_retries` | numero | `3` | tentativi |
| `self_op.on_deop` | sì/no | `true` | lo richiede se glielo tolgono |
| `self_op.channels` | lista | tutti | in quali canali |

## about

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `about.intro` | lista di testi | una frase di presentazione | presentazione del bot |
| `about.auto_features` | sì/no | `true` | aggiunge l'elenco delle funzioni attive |
| `about.method` | testo | `private` | `private`, `notice`, `say` per le spiegazioni lunghe |
| `about.inline_max_lines` | numero | `2` | le risposte più corte restano dove è stata fatta la domanda |
| `about.topics.<nome>` | testo, lista o `{text, aliases}` | — | argomenti spiegati da `!info <nome>` |
| `about.texts` | mappa | vedi [testi](#testi-predefiniti) | testi |

## search

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `search.enabled` | sì/no | `false` | ricerca di risposte tecniche |
| `search.timeout` | secondi | `8` | per provider |
| `search.cache_minutes` | minuti | `60` | la stessa ricerca non va all'API per così tanto |
| `search.max_results` | numero | `3` | risultati mostrati, a turno fra i provider |
| `search.min_query_length` | numero | `3` | caratteri minimi della ricerca |
| `search.only_answered` | sì/no | `true` | Stack Exchange: solo domande con risposte |
| `search.stackexchange_key` | testo | nessuna | chiave Stack Exchange (10000 richieste al giorno invece di 300) |
| `search.providers` | lista | unix.SE, SO, Ask Ubuntu, ArchWiki | fonti, vedi sotto |
| `search.providers[].name` | testo | il sito | nome (per `providers: [...]` nell'azione) |
| `search.providers[].type` | testo | **obbligatorio** | `stackexchange`, `mediawiki`, `youtube` |
| `search.providers[].label` | testo | il nome | etichetta nei risultati |
| `search.providers[].site` | testo | — | stackexchange: `stackoverflow`, `it.stackoverflow`, `unix`, `askubuntu`... |
| `search.providers[].api` | URL | — | mediawiki: indirizzo di `api.php` |
| `search.providers[].channels` | lista | — | youtube: handle `@nome` o ID `UC...` |
| `search.providers[].api_key` | testo | nessuna | youtube: chiave Data API v3 (senza: ultimi 15 video, servono gli ID) |
| `search.providers[].order` | testo | `relevance` | youtube: `relevance` o `date` |
| `search.texts` | mappa | vedi [testi](#testi-predefiniti) | testi |

## karma

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `karma.enabled` | sì/no | `false` | karma del canale |
| `karma.scope` | testo | `channel` | `channel` (classifica per canale) o `global` |
| `karma.channels` | lista | tutti | dove è attivo |
| `karma.plusplus` | sì/no | `true` | riconosce `nick++` e `nick--` |
| `karma.allow_negative` | sì/no | `true` | permette `nick--` |
| `karma.require_present` | sì/no | `true` | solo per chi è nel canale (così `c++` non conta) |
| `karma.ignore_words` | lista | `c g i j k n x y` | parole mai considerate nick |
| `karma.max_per_message` | numero | `3` | `nick++` contati per messaggio |
| `karma.cooldown` | secondi | `3600` | stesso donatore → stesso destinatario |
| `karma.daily_limit` | numero | `20` | punti che ognuno può dare in 24 ore |
| `karma.reset_on_ban` | sì/no | `true` | un ban azzera il karma |
| `karma.announce` | sì/no | `true` | annuncia ogni cambio |
| `karma.announce_reset` | sì/no | `false` | annuncia l'azzeramento per ban |
| `karma.top_size` | numero | `10` | lunghezza della classifica |
| `karma.texts` | mappa | vedi [testi](#testi-predefiniti) | testi |

## schedules

Attività programmate. Lista di:

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `schedules[].name` | testo | `scheduleN` | nome (per `!esegui`, log, stato) |
| `schedules[].at` | `"HH:MM"` o lista | — | orari (serve `at` o `every`) |
| `schedules[].every` | durata (testo) | — | intervallo, minimo `1m` |
| `schedules[].days` | lista | tutti | `lun mar mer gio ven sab dom` (anche per esteso o in inglese) |
| `schedules[].channels` | lista | tutti quelli in cui è il bot | dove eseguire le azioni |
| `schedules[].per_channel` | sì/no | `true` | `false`: una volta sola, senza canale (es. backup) |
| `schedules[].actions` | lista di azioni | **obbligatorio** | cosa fare |
| `schedules[].catch_up` | durata (testo) | `10m` | se il bot era spento, recupera entro così |
| `schedules[].late_text` | testo | ` (prevista alle {scheduled})` | valore di `{late}` quando è in ritardo |
| `schedules[].enabled` | sì/no | `true` | per disattivarla |

## logging

| Chiave | Tipo | Predefinito | Significato |
|---|---|---|---|
| `logging.level` | testo | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.console` | sì/no | `true` | a video (con systemd va nel journal) |
| `logging.format` | testo | `text` | `text` o `json` |
| `logging.levels.<area>` | livello | quello generale | per area: `connection irc auth channels commands greetings memory bans op events config stats` |
| `logging.redact` | lista di regex | `[]` | testo da nascondere (le password della config lo sono già) |
| `logging.slow_threshold` | secondi | `0.5` | righe e azioni più lente: avviso |
| `logging.slow_db_threshold` | secondi | `0.05` | operazioni sul database più lente: avviso |
| `logging.file.path` | percorso | nessuno | file di log (anche `file: logs/bot.log` come forma breve) |
| `logging.file.rotate` | testo | `size` | `size`, `daily`, `none` |
| `logging.file.max_bytes` | dimensione | `10MB` | con `rotate: size` |
| `logging.file.backups` | numero | `7` | file vecchi da tenere |
| `logging.alerts.enabled` | sì/no | `false` | avvisi su IRC agli admin |
| `logging.alerts.level` | livello | `ERROR` | da quale livello |
| `logging.alerts.to` | lista | — (obbligatoria se attivi) | canali e/o nick |
| `logging.alerts.method` | `notice` / `say` | `notice` | come avvisare |
| `logging.alerts.min_interval` | secondi | `300` | stesso messaggio al massimo ogni N secondi |
| `logging.alerts.max_per_hour` | numero | `20` | avvisi all'ora al massimo |
| `logging.chatlog.enabled` | sì/no | `false` | log delle conversazioni |
| `logging.chatlog.directory` | percorso | `logs/chat` | cartella (un file al giorno per canale) |
| `logging.chatlog.channels` | lista | tutti | quali canali |
| `logging.chatlog.private` | sì/no | `false` | anche i messaggi privati |
| `logging.chatlog.retention_days` | giorni | `90` | 0 = per sempre |

## Azioni

Le azioni si usano in `commands`, `responses`, `greetings`, `events` e `schedules`. Ognuna è
una mappa con una sola chiave: forma breve (`- say: "testo"`) o estesa (`- say: {to, text}`).
Senza indicazioni il canale è quello del messaggio e il nick quello di chi l'ha scritto.

| Azione | Forma breve | Forma estesa / note |
|---|---|---|
| `say` | `say: "testo"` | `{to, text}`; più righe = più messaggi |
| `reply` | come `say` | alias |
| `msg` | come `say` | alias |
| `notice` | `notice: "testo"` (all'utente) | `{to, text}` |
| `me` | `me: "balla"` | `{to, text}` (azione /me) |
| `kick` | `kick: "{arg1}"` | `{channel, nick, reason}`; mai su immuni |
| `ban` | `ban: "{arg1}"` | `{channel, nick\|mask, duration, reason, mask_type, confirm}` |
| `kickban` | come `ban` | banna ed espelle (con una maschera: tutti quelli che corrispondono) |
| `unban` | `unban: "{arg1}"` | nick (anche uscito), maschera o `#id` |
| `op` | `op: "{nick}"` | `{channel, nick}` |
| `deop` | come `op` | |
| `halfop` | come `op` | |
| `dehalfop` | come `op` | |
| `voice` | come `op` | |
| `devoice` | come `op` | |
| `mode` | `mode: "#canale +m"` | `{target, modes}` |
| `topic` | `topic: "{args}"` | `{channel, text}` |
| `invite` | `invite: "{arg1}"` | `{channel, nick}` |
| `join` | `join: "#canale [chiave]"` | `{channel, key}`; temporaneo (vedi `channel_create`) |
| `part` | `part: "#canale"` | `{channel, reason}` |
| `nick` | `nick: "NuovoNick"` | |
| `raw` | `raw: "PRIVMSG X :ciao"` | riga IRC grezza |
| `delay` | `delay: 2` | attesa in secondi fra un'azione e l'altra |
| `help` | `help` | `{method, topic}`: elenco dei comandi o spiegazione di uno |
| `reload` | `reload` | `{ok, error}`: ricarica la configurazione |
| `quit` | `quit: "motivo"` | disconnette il bot |
| `seen` | `seen: "{arg1}"` | `{nick}` |
| `recent` | `recent: "{arg1}"` | `{days, hours, channel, all_channels}` |
| `memo_add` | — | `{to, text}` |
| `memo_read` | `memo_read` | consegna i messaggi a chi chiede |
| `banlist` | `banlist` | `{channel}` |
| `baninfo` | `baninfo: "{arg1}"` | `{nick\|mask, channel}` |
| `stats` | `stats` | `{section, method}` |
| `explain` | `explain: "{args}"` | `{topic, method}`; vuoto = presentazione |
| `search` | `search: "{args}"` | `{query, providers}` |
| `karma_change` | `karma_change: "{arg1}"` | `{nick, delta: 1\|-1, quiet}` |
| `karma_show` | `karma_show: "{target}"` | `{nick}` |
| `karma_top` | `karma_top` | `{size, worst, skip_empty, text}` |
| `karma_reset` | `karma_reset: "{arg1}"` | `{nick}` |
| `schedule_run` | `schedule_run: "{arg1}"` | `{name}` |
| `db_backup` | `db_backup: backups` | `{directory, keep}` |
| `channel_check` | `channel_check: "{arg1}"` | controlla topic, modi e descrizione |
| `channel_create` | — | `{channel, template, key}` |
| `channel_close` | — | `{channel, confirm}` |
| `channel_list` | `channel_list` | stato dei canali |

## Variabili nei testi

| Variabile | Valore |
|---|---|
| `{nick}` `{user}` `{host}` `{mask}` | chi ha scritto o è entrato |
| `{channel}` | il canale (vuoto in privato) |
| `{in_channel}` | " di #canale", o vuoto in privato |
| `{botnick}` `{server}` `{version}` | il bot, il server, la versione del bot |
| `{date}` `{time}` `{weekday}` | data, ora e giorno (in `behavior.timezone`) |
| `{users}` | utenti nel canale |
| `{prefix}` | primo prefisso dei comandi |
| `{args}` `{arg1}`...`{arg9}` | argomenti del comando |
| `{rest}` `{rest2}` | argomenti dal 2° e dal 3° in poi |
| `{target}` `{argc}` `{command}` | `{arg1}` o `{nick}`; numero di argomenti; il comando |
| `{match}` `{group1}`... `{nome}` | risposte con regex |
| `{schedule}` `{late}` | attività programmate |
| `{reason}` `{victim}` `{kicker}` `{old_nick}` `{new_nick}` `{topic}` | eventi |
| le chiavi di `variables` | i tuoi valori |

Filtri: `{args|url}` (link di ricerca), `{arg1|path}`, `|lower`, `|upper`, `|strip`, `|title`,
anche combinati (`{arg1|lower|url}`). `{{` e `}}` scrivono una graffa. Un segnaposto
sconosciuto resta com'è.

## Testi predefiniti

Le sezioni `memory.texts`, `bans.texts`, `karma.texts`, `search.texts` e `about.texts`
accettano queste chiavi; basta indicare quelle da cambiare. Questa tabella è generata dal
codice con `python contrib/gen-docs.py`.

<!-- TESTI:INIZIO -->

### `memory.texts`

| Chiave | Testo predefinito |
|---|---|
| `seen_here` | {who} è qui adesso, in {where}. |
| `seen` | {who} è stato visto l'ultima volta {ago} ({when}) in {where}, {what}. |
| `seen_never` | Non ho mai visto {who}. |
| `seen_self` | Sei proprio qui, {nick}! |
| `seen_bot` | Io sono sempre qui! |
| `seen_usage` | Di chi vuoi sapere? Uso: {prefix}seen <nick> |
| `event_join` | mentre entrava |
| `event_part` | mentre usciva ({detail}) |
| `event_quit` | mentre si disconnetteva ({detail}) |
| `event_kick` | mentre veniva espulso ({detail}) |
| `event_nick` | mentre cambiava nick in {detail} |
| `event_nick_from` | dopo aver cambiato nick da {detail} |
| `event_message` | mentre scriveva |
| `event_message_text` | mentre scriveva: {detail} |
| `recent` | Passati {period} ({count}): {names} |
| `recent_none` | Non è passato nessuno {period}. |
| `recent_more` | e altri {count} |
| `memo_saved` | Ok {nick}, riferirò il messaggio a {recipient} appena lo vedo. |
| `memo_usage` | Uso: {prefix}lascia <nick> <messaggio> |
| `memo_self` | {nick}, non puoi lasciare un messaggio a te stesso. |
| `memo_bot` | {nick}, il messaggio per me l'ho già letto ;) |
| `memo_too_long` | {nick}, il messaggio è troppo lungo (massimo {max} caratteri). |
| `memo_full` | {nick}, {recipient} ha già troppi messaggi in attesa. |
| `memo_delivered` | {recipient}, {sender} ti ha lasciato un messaggio {ago}: {text} |
| `memo_none` | Nessun messaggio per te, {nick}. |

### `bans.texts`

| Chiave | Testo predefinito |
|---|---|
| `kick_reason` | {reason} |
| `kick_reason_temp` | {reason} (ban per {duration}) |
| `default_reason` | Bannato da {nick} |
| `ban_done` | Ban impostato su {mask} in {channel}, {expiry}. |
| `unban_done` | Ban rimosso: {mask} in {channel}. |
| `unban_unknown` | Non ho ban registrati per {who} in {channel}: provo a togliere {mask}. |
| `bad_duration` | Durata non valida: {duration}. Esempi: 30m, 2h, 1d, 1w, 1d12h, perm. |
| `too_long` | La durata massima di un ban è {max}. |
| `no_channel` | In quale canale? Indica il canale, es. #linux. |
| `list_header` | Ban attivi in {channel} ({count}): |
| `list_item` | #{id} {mask}{who} da {setter} {ago}, {expiry}{why} |
| `list_more` | ...e altri {count}. Usa {prefix}baninfo <nick\|maschera\|#id> per i dettagli. |
| `list_empty` | Nessun ban attivo in {channel}. |
| `info` | #{id} {mask} in {channel}{who}: messo da {setter} il {when} ({ago}), {expiry}{why} |
| `info_removed` | Nessun ban attivo per {who}. L'ultimo (#{id} {mask}) è stato tolto {removed_how} {removed_ago}. |
| `info_none` | Nessun ban registrato per {who}. |
| `expiry_permanent` | permanente |
| `expiry_in` | scade fra {remaining} |
| `enforce_reason` | Sei bannato da questo canale{why} |
| `immune` | {who} è immune a ban e kick in {channel}. |

### `karma.texts`

| Chiave | Testo predefinito |
|---|---|
| `changed` | {target} ha ora {points} karma ({sign}1 da {nick}) |
| `self` | {nick}: il karma non si dà da soli ;) |
| `bot` | Grazie {nick}, ma io non gioco: il karma è per gli umani. |
| `cooldown` | Hai già dato karma a {target} da poco: riprova fra {wait}. |
| `daily_limit` | Per oggi hai già dato {limit} punti karma, riprova domani. |
| `show` | {target} ha {points} karma (+{plus}/-{minus}){rank}. |
| `show_rank` | , {rank}° su {total}{where} |
| `show_none` | {target} non ha ancora punti karma. |
| `top` | Classifica karma{where}: {list} |
| `top_item` | {pos}. {nick} ({score}) |
| `top_empty` | Nessuno ha ancora punti karma{where}. |
| `reset` | Karma di {target} azzerato (era {old}). |
| `reset_none` | {target} non ha punti karma da azzerare. |
| `reset_on_ban` | Karma di {target} azzerato per il ban (era {old}). |

### `search.texts`

| Chiave | Testo predefinito |
|---|---|
| `result` | [{label}] {title} {meta}{link} |
| `meta` | ({accepted}{votes}, {replies})  |
| `video_meta` | ({channel}, {date})  |
| `accepted` | ✔  |
| `none` | {nick}: non ho trovato niente per "{query}". Prova a riformulare, magari in inglese. |
| `error` | {nick}: la ricerca non è disponibile in questo momento, riprova più tardi. |
| `partial` | (non ho potuto cercare su: {failed}) |
| `usage` | {nick}: cosa devo cercare? Es. {prefix}so come montare un disco usb |
| `too_short` | {nick}: dammi qualche parola in più da cercare. |

### `about.texts`

| Chiave | Testo predefinito |
|---|---|
| `redirect` | {nick}: ti ho spiegato tutto in privato. |
| `unknown` | Non so spiegarti "{topic}". Puoi chiedermi di: {topics}. |
| `features_header` | Ecco cosa so fare: |
| `commands_hint` | Scrivi {prefix}help per l'elenco dei comandi, oppure "{botnick}: come si usa <comando>?" o {prefix}info <comando\|argomento> per i dettagli. |
| `topics_hint` | Altri argomenti: {topics}. |
| `examples` | Esempi: {examples} |
| `aliases` | Puoi scriverlo anche: {aliases}. |
| `who_can` | Chi può usarlo: {who}. |
| `not_for_you` | Questo comando non puoi usarlo: è riservato, lo usano {who}. |
| `private_only` | Si usa solo nei canali, non in privato. |
| `help_anyone` | Comandi: {commands} |
| `help_voice` | Per chi ha voice: {commands} |
| `help_halfop` | Per gli halfop: {commands} |
| `help_op` | Per gli operatori: {commands} |
| `help_admin` | Per gli admin del bot: {commands} |
| `help_hint` | Per sapere come si usa un comando: {prefix}help <comando> (es. {prefix}help {example}). Per sapere chi sono e cosa so fare: "{botnick}: chi sei?" |
<!-- TESTI:FINE -->
