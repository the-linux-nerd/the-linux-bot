# Funzioni del bot

Come funziona ogni parte del bot, con esempi. Per configurarlo da zero vedi la
[guida alla configurazione](configurazione.md); per l'elenco completo delle opzioni il
[riferimento](riferimento.md).

- [Comandi, risposte e azioni](#comandi-risposte-e-azioni)
- [Il saluto e la "@"](#il-saluto-e-la-)
- [Il bot si spiega agli utenti](#il-bot-si-spiega-agli-utenti)
- [Link rapidi](#link-rapidi)
- [Ricerca di risposte tecniche](#ricerca-di-risposte-tecniche)
- [Memoria: "seen", chi è passato, messaggi lasciati](#memoria-seen-chi-è-passato-messaggi-lasciati)
- [Karma](#karma)
- [Ban](#ban)
- [Diventare operatore](#diventare-operatore)
- [Identificazione e nick occupato](#identificazione-e-nick-occupato)
- [Topic, modi e descrizione del canale](#topic-modi-e-descrizione-del-canale)
- [Canali multipli e canali creati da IRC](#canali-multipli-e-canali-creati-da-irc)
- [Attività programmate](#attività-programmate)
- [Memoria fra un riavvio e l'altro](#memoria-fra-un-riavvio-e-laltro)
- [Log, debug e statistiche](#log-debug-e-statistiche)
- [Avvio automatico come servizio](#avvio-automatico-come-servizio)

## Comandi, risposte e azioni

Tutte le opzioni, con tipi e valori predefiniti, sono nel [riferimento](riferimento.md).

Il file [`config.example.yaml`](../config.example.yaml) è commentato riga per riga e contiene
un esempio di tutto. In sintesi:

| Sezione      | Cosa contiene |
|--------------|---------------|
| `server`     | host, porta, TLS, password, encoding |
| `identity`   | nick, nick alternativi, username, realname, risposta CTCP VERSION |
| `auth`       | `sasl`, `nickserv`, `oper`, `user_modes` |
| `self_op`    | come il bot ottiene e mantiene l'op nei canali |
| `about`      | come il bot si presenta e spiega comandi e funzioni |
| `search`     | ricerca su Stack Exchange, wiki MediaWiki e canali YouTube |
| `karma`      | karma del canale e relative regole |
| `schedules`  | attività a orari fissi o a intervalli |
| `channels`   | canali (nome o `{name, key, owners, topic, topic_lock, modes, modes_lock, description}`) |
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
    details:                # righe in più per "come si usa kick?" / !info kick
      - "Il motivo è facoltativo e compare nel messaggio di espulsione."
    examples: ["!kick mario basta flood"]
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
| `help` | `help` / `help: say` | `help: {method: notice\|say\|private, topic}`: elenco o spiegazione di un comando |
| `reload` | `reload` | `reload: {ok: "...", error: "... {error}"}` |
| `quit` | `quit: "motivo"` | |
| `explain` | `explain: "{args}"` (vuoto = presentazione) | `explain: {topic, method}` |
| `search` | `search: "{args}"` | `search: {query, providers: [unix, so]}` |
| `karma_change` | `karma_change: "{arg1}"` | `karma_change: {nick, delta, quiet}` |
| `karma_show` / `karma_top` / `karma_reset` | `karma_show: "{target}"` | `karma_top: {size, worst, skip_empty, text}` |
| `schedule_run` | `schedule_run: "{arg1}"` | `schedule_run: {name}` |
| `stats` | `stats` | `stats: {section, method}` |
| `seen` | `seen: "{arg1}"` | `seen: {nick}` |
| `recent` | `recent: "{arg1}"` (giorni) | `recent: {days, hours, channel, all_channels}` |
| `activity_map` | `activity_map: "{arg1}"` (giorni) | `activity_map: {days, channel, all_channels, chars, slot}` |
| `memo_add` | | `memo_add: {to: "{arg1}", text: "{rest}"}` |
| `memo_read` | `memo_read` | |

### Eventi

`connect`, `identified`, `self_join`, `self_op`, `self_deop`, `self_kick`, `join`, `part`,
`quit`, `nick`, `kick`, `topic`, `invite`. Variabili extra: `{reason}`, `{victim}`, `{kicker}`, `{old_nick}`, `{new_nick}`, `{topic}`.

## Il saluto e la "@"

Il saluto scrive il nick così com'è (`Ciao mario, benvenuto…`): i client IRC lo evidenziano
già. Se preferisci lo stile `@mario` scrivi `@{nick}` nel testo. La `@` che compare davanti al
nick nella lista utenti è invece lo stato di operatore: la dà l'azione `op`, che nell'esempio
viene usata solo per gli host fidati (`greetings.rules`).

## Il bot si spiega agli utenti

Se gli si chiede chi è o come funziona, il bot risponde con una presentazione (`about.intro`)
seguita dall'elenco di **ciò che sa fare davvero**, costruito dalla configurazione: compaiono
solo le funzioni attive (saluti, memoria, messaggi, domande a cui risponde, moderazione) e,
per ognuna, i comandi che l'utente può usare.

```
<anna> TheLinuxBot: chi sei?
<TheLinuxBot> anna: ti ho spiegato tutto in privato.
  (in privato)
  Ciao anna! Sono TheLinuxBot, il bot della community The Linux Nerd di #linux.
  Ecco cosa so fare:
  - Ricordo chi passa nei canali. Chiedimi "quando è passato mario?" ... (oppure !seen <nick>, !passati [giorni]).
  - Posso riferire un messaggio a chi non c'è: ...
  - Rispondo da solo ad alcune domande: quale distro scegliere, dove incollare log ed errori lunghi.
  - Aiuto gli operatori a moderare: ...
  Scrivi !help per l'elenco dei comandi, oppure "TheLinuxBot: come si usa <comando>?" ...
<anna> TheLinuxBot, come si usa seen?
  (in privato)
  !seen <nick> — Quando è stato visto l'ultima volta un utente
  Ti dico quando ho visto quell'utente l'ultima volta, dove e cosa stava facendo ...
  Esempi: !seen mario
  Puoi scriverlo anche: !visto.
```

- **`!help`** elenca i comandi che l'utente può usare, divisi per livello (per tutti, per gli
  operatori, per gli admin); **`!help <comando|argomento>`** dà la spiegazione completa, la stessa
  di `!info`. "TheLinuxBot: aiuto" o "help" scritto in privato danno la presentazione.
- **Come si attiva**: con l'azione `explain`, collegata a un comando (`!info [comando|argomento]`)
  e a frasi come "chi sei", "cosa sai fare", "come si usa X", "spiegami X" (vedi `responses`
  nella config d'esempio). "come si usa !tempban?" funziona anche senza citare il bot.
- **Comandi**: la spiegazione usa `description`, `usage`, `details` (righe in più) ed
  `examples`, più gli alias e chi può usarlo ("Questo comando non puoi usarlo: è riservato, lo
  usano gli operatori del canale"). I comandi `hidden` non vengono spiegati, tranne agli admin.
- **Argomenti**: oltre a quelli automatici (`saluti`, `memoria`, `messaggi`, `domande`,
  `moderazione`) puoi scriverne di tuoi in `about.topics`, con alias; hanno la precedenza.
  Le risposte automatiche compaiono tra le "domande" se hanno una `description`.
- **Dove risponde**: le spiegazioni brevi (fino a `inline_max_lines` righe) restano nel canale;
  quelle lunghe vanno in privato (`method: private`), in notice o nel canale (`say`), con un
  breve avviso nel canale. Tutti i testi fissi si cambiano in `about.texts`.

## Link rapidi

Sono normali comandi con un `reply`, quindi si aggiungono o cambiano nel YAML. Due strumenti
li rendono comodi:

- **filtri nei testi**: `{args|url}` codifica il testo per un link di ricerca (spazi, `&`...),
  `{arg1|path}` per un pezzo di percorso, e poi `|lower`, `|upper`, `|strip`, `|title`
  (combinabili: `{arg1|lower|url}`);
- **`args_pattern`**: una regex che gli argomenti devono rispettare, altrimenti il bot risponde
  `Uso: ...`. Ad esempio `!rfc` accetta solo numeri e `!cve` solo `CVE-AAAA-NNNN`.

```yaml
commands:
  archwiki:
    aliases: [aw]
    usage: "<argomento>"
    min_args: 1
    reply: "{nick}: https://wiki.archlinux.org/index.php?search={args|url}"
  cve:
    usage: "<CVE-AAAA-NNNN>"
    args_pattern: '(?i)^cve-\d{4}-\d{4,}$'
    reply: "{arg1|upper}: https://nvd.nist.gov/vuln/detail/{arg1|upper}"
```

## Ricerca di risposte tecniche

Con `search.enabled` il bot cerca domande già risolte sui siti Stack Exchange (`type:
stackexchange`, con `site`: `stackoverflow`, `it.stackoverflow`, `unix`, `askubuntu`,
`superuser`, `serverfault`...) e nelle wiki MediaWiki (`type: mediawiki`, con l'URL di `api.php`,
ad esempio l'ArchWiki).

Esempio di formato (titoli e numeri sono illustrativi):

```
<anna> TheLinuxBot: come faccio a escludere una cartella con rsync?
<TheLinuxBot> [unix.SE] How to exclude a directory with rsync (✔ 412 voti, 9 risposte) https://unix.stackexchange.com/q/...
<TheLinuxBot> [ArchWiki] Rsync https://wiki.archlinux.org/title/Rsync
<TheLinuxBot> [SO] ... https://stackoverflow.com/q/...
```

- Si attiva con l'azione `search` (`!cerca <domanda>`, `!unix <domanda>` solo su unix.SE),
  con `?? domanda` e con "come faccio a / how do I ..." rivolto al bot.
- I provider vengono interrogati in parallelo e i risultati presi a turno (uno per fonte), fino
  a `max_results`; `✔` indica una risposta accettata. Con `only_answered` si vedono solo
  domande con almeno una risposta.
- **Limiti dell'API**: senza chiave Stack Exchange concede circa 300 richieste al giorno per
  indirizzo IP, e ogni sito conta una richiesta. Con una chiave gratuita
  (https://stackapps.com, in `search.stackexchange_key` o nella variabile `STACKEXCHANGE_KEY`)
  si arriva a 10000. Il bot tiene in cache le ricerche per `cache_minutes`, rispetta le pause
  (`backoff`) richieste dall'API e avvisa nel log quando la quota sta finendo.
- Se un sito non risponde entro `timeout` secondi, il bot mostra gli altri risultati e lo dice.
  Il bot deve poter raggiungere `api.stackexchange.com` e le wiki configurate.
- Le ricerche funzionano meglio in inglese: le domande in italiano trovano soprattutto
  risultati su `it.stackoverflow`.

### Video da canali YouTube

Un provider `type: youtube` cerca fra i video dei canali indicati (handle `@nome` o ID `UC...`):

```yaml
search:
  providers:
    - name: youtube
      type: youtube
      label: "YouTube"
      api_key: ${YOUTUBE_API_KEY:-}
      order: relevance            # o date, per i più recenti
      channels: ["@the-linux-nerd", "UCxxxxxxxxxxxxxxxxxxxxxx"]
```

```
<anna> c'è un video su btrfs?
<TheLinuxBot> [YouTube] titolo del video (The Linux Nerd, 01/10/2026) https://youtu.be/...
```

- **Con `api_key`** (YouTube Data API v3, gratuita: si crea su https://console.cloud.google.com
  abilitando "YouTube Data API v3") il bot cerca fra **tutti** i video del canale. La quota è di
  10000 unità al giorno e ogni ricerca costa 100 unità per canale: circa 100 ricerche al giorno
  con un canale, 50 con due. L'handle viene convertito nell'ID una volta sola.
- **Senza chiave** il bot legge il feed pubblico del canale, che contiene **solo gli ultimi 15
  video**, e cerca le parole della domanda in titoli e descrizioni (senza badare a maiuscole e
  accenti). In questo modo servono gli ID `UC...`: l'handle si può convertire solo con la chiave.
  Un canale indicato per handle senza chiave viene saltato, con un avviso nel log.
- Nella config d'esempio YouTube fa parte di `!cerca`, e c'è `!video` (`!yt`) per cercare solo
  nei video, più la frase "c'è un video su ...?". Il bot deve poter raggiungere
  `www.googleapis.com` (con la chiave) o `www.youtube.com` (senza).

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

Le azioni disponibili sono `seen`, `recent`, `activity_map`, `memo_add`, `memo_read` (vedi tabella sotto), quindi
puoi collegarle a qualsiasi comando o frase. Tutti i testi si cambiano in `memory.texts`; le chiavi sono:
`seen`, `seen_here`, `seen_never`, `seen_self`, `seen_bot`, `seen_usage`, `event_join`, `event_part`,
`event_quit`, `event_kick`, `event_nick`, `event_nick_from`, `event_message`, `event_message_text`,
`recent`, `recent_none`, `recent_more`, `activity_title`, `activity_period_days`, `activity_period_all`,
`activity_everywhere`, `activity_days`, `activity_hour`, `activity_best`, `activity_none`, `memo_saved`, `memo_usage`, `memo_self`, `memo_bot`,
`memo_too_long`, `memo_full`, `memo_delivered`, `memo_none`.

### Mappa dell'attività

Con la memoria accesa il bot conta i messaggi di ogni canale per giorno e per ora (solo il numero:
niente nick, niente testo). `!mappaattivita` serve a chi vuole sapere quando connettersi per
trovare gente: disegna i giorni della settimana per fasce di quattro ore, con il totale di ogni
giorno, e chiude con le tre fasce più vive:

```
Quando c'è gente in #the-linux-nerd (69 messaggi dal 22/07/2024).
ore 00-04 04-08 08-12 12-16 16-20 20-24
Lun  ░░░    ·     ·     ·     ·    ███   40
Mar  ░░░    ·    ░░░   ░░░   ░░░   ░░░   11
Mer  ░░░    ·    ░░░   ░░░    ·     ·    11
...
Dom   ·     ·     ·     ·     ·     ·    0
Meglio venire: Lun 20-24 · Mer 00-04 · Mar 08-12.
```

`!mappaattivita 30` guarda solo gli ultimi 30 giorni. Le opzioni dell'azione: `days`, `channel`,
`all_channels` (tutti i canali insieme), `chars`, la scala dal vuoto al pieno (`·░▒▓█`; il primo
carattere segna le fasce vuote), e `slot`, le ore di ogni fascia (1, 2, 3, 4, 6, 8 o 12; 4 di default).
Non contano i messaggi del bot, quelli degli utenti in `behavior.ignore`, i privati e le azioni /me.
L'ora è quella locale del bot. I conteggi non scadono con `retention_days`: sono pochi e anonimi.

Per partire con lo storico, a bot fermo si importano i log che ci sono già:

```bash
python -m linuxbot -c config.yaml --import-activity ~/.local/share/weechat/logs/irc.libera.#canale.weechatlog
python -m linuxbot -c config.yaml --import-activity chatlog/   # la chatlog del bot, cartella o file
```

Il canale si ricava dal nome del file (`--channel '#canale'` se non si può). Per i giorni contenuti
nei log l'import sostituisce i conteggi: rifarlo non raddoppia niente.

## Karma

Con `karma.enabled` gli utenti si ringraziano scrivendo `mario++` (anche più d'uno nello stesso
messaggio) o "grazie mario"; `mario--` toglie un punto. I punteggi stanno nel database e sono
permanenti.

```
<anna> mario++ grazie, funziona!
<TheLinuxBot> mario ha ora 13 punti karma (+1 da anna)
<anna> !karma mario
<TheLinuxBot> mario ha 13 punti karma (+15/-2), 2° su 40 in #linux.
<anna> !top
<TheLinuxBot> Classifica karma in #linux: 1. carla (21), 2. mario (13), ...
```

- **Regole anti-abuso**: niente karma a sé stessi; lo stesso utente può dare karma alla stessa
  persona una volta ogni `cooldown` secondi (default un'ora) e al massimo `daily_limit` punti in
  24 ore; il destinatario deve essere nel canale (`require_present`), così `c++`, `g++` o `i++`
  scritti parlando di codice non contano; `ignore_words` esclude altre parole.
- **Azzeramento al ban**: quando nel canale viene messo un ban (dal bot, con `!ban` o
  `!tempban`, o a mano da un op), il karma di chi ne è colpito torna a zero, anche se è già
  uscito: il bot confronta la maschera del ban con l'ultimo host noto di ogni utente con karma.
  Il karma si azzera solo quando il server conferma il ban. Gli immuni (admin, owner) non vengono
  toccati. `!karmareset <nick>` (per gli op) lo azzera a mano.
- **Ambito**: `scope: channel` tiene una classifica per canale, `scope: global` una sola;
  `channels` limita il karma ad alcuni canali.
- Il karma è legato al **nick**: un cambio di nick non lo porta con sé.
- **Top ten di mezzanotte**: nella config d'esempio un'attività programmata
  (`schedules`, vedi sotto) pubblica ogni giorno alle 00:00 la top ten di `#linux`.
- Azioni: `karma_change` (`{nick, delta: 1|-1, quiet}`), `karma_show`, `karma_top` (`{size,
  worst, skip_empty, text}`), `karma_reset`. Testi in `karma.texts`.

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

## Identificazione e nick occupato

Non serve sapere quali services usa il server (Anope, Atheme...): il bot si regola da solo.

1. **SASL automatico** (`auth.sasl.enabled: auto`, il default): se il server offre SASL PLAIN,
   il bot si identifica già durante la connessione con l'account e la password di NickServ.
   SASL identifica l'account, non il nick, quindi funziona anche con un nick alternativo. Se il
   server non lo offre o SASL fallisce, si passa a NickServ.
2. **NickServ**: con `command: auto` manda `IDENTIFY <password>` se ha il nick dell'account,
   altrimenti `IDENTIFY <account> <password>` (accettato da Anope e Atheme da qualunque nick).
3. **Riconoscimento dei services**: chiede la versione a NickServ (CTCP VERSION) e la scrive nel
   log (`Services: atheme (atheme-7.2.12)`). Si può anche fissare con `services: anope|atheme`.
4. **Nick occupato** (tipico dopo una caduta della connessione, quando il server tiene ancora il
   vecchio collegamento): dopo l'identificazione il bot se lo riprende con `REGAIN` (Atheme) o
   `RECOVER` (Anope); con services sconosciuti prova `REGAIN`, `RECOVER` e `GHOST`, passando al
   successivo se NickServ risponde "comando sconosciuto". Entra nei canali solo dopo, così
   ChanServ gli dà l'op. Se non ci riesce resta col nick alternativo e si riprende quello
   principale appena chi lo usa esce o cambia nick. `regain_commands` permette di indicare
   comandi propri, `regain: false` disattiva il recupero.

## Topic, modi e descrizione del canale

Per ogni canale puoi indicare il topic (il "titolo"), i modi e la descrizione che deve avere:

```yaml
channels:
  - name: "#linux"
    topic: "Benvenuti su #linux | Regole: {rules_url}"
    topic_lock: false
    modes: "+nt"
    modes_lock: false
    description: "Canale italiano di aiuto su Linux e open source"
```

- **Topic**: quando entra (e quando riceve l'op) il bot confronta il topic attuale con quello
  configurato, senza badare agli spazi, e lo imposta se è diverso. Serve l'op: se non ce l'ha
  lo scrive nel log e lo fa appena lo riceve. Nel testo si possono usare le variabili
  (`{rules_url}`, `{channel}`...).
- **`topic_lock: true`**: se qualcuno cambia il topic, il bot rimette quello configurato (anche
  se a cambiarlo è un op: per un topic nuovo si modifica il file e si fa `!reload`). Con
  `false` il bot imposta il topic quando entra e poi lascia liberi gli op.
- **Descrizione**: non è un'impostazione di IRC ma di ChanServ (`/msg ChanServ INFO #canale`).
  Il bot chiede `INFO`, legge la riga "Description" e, se è diversa, manda `SET DESC` (Anope).
  Atheme non ha una descrizione dei canali: il bot lo dice nel log e non fa niente. Il bot deve
  avere il permesso di modificare il canale su ChanServ (founder, o flag/livello adatto); se non
  ce l'ha, la risposta di ChanServ finisce nel log. `description_command` permette un comando
  diverso (es. `"SET {channel} URL https://..."` su Atheme).
- **Modi** (`modes`, sintassi IRC: `"+nt"`, `"+ntC-s"`, `"+ntl 80"`, `"+k chiave"`): il bot
  chiede al server i modi attuali (`MODE #canale`), li confronta con quelli configurati e manda
  solo le modifiche necessarie (es. `MODE #linux +tC-sm`); i modi non nominati non vengono
  toccati. I tipi di modo (con o senza parametro) li dice il server (`CHANMODES`), quindi vale
  per qualsiasi ircd; se una lettera non esiste il server la rifiuta e il bot lo scrive nel log.
  Ban, eccezioni e modi degli utenti (`b`, `e`, `I`, `o`, `v`...) non si mettono qui.
- **`modes_lock: true`**: se qualcuno cambia un modo configurato, il bot lo rimette. Se i
  modi vengono cambiati di continuo (tipicamente un `MLOCK` di ChanServ diverso dal file),
  dopo 3 ripristini in un minuto il bot si ferma e lo segnala, invece di fare a botta e
  risposta all'infinito. Nei log la chiave `+k` non compare.
- La descrizione si controlla una volta per connessione; tutto viene ricontrollato dopo un
  `!reload`, e `!canale [#canale]` (admin) forza il controllo subito.

## Canali multipli e canali creati da IRC

Il bot può stare in quanti canali vuoi: elencali in `channels`, ognuno con le sue impostazioni
(chiave, owner immuni, topic, modi, descrizione). Molte funzioni si possono limitare ad alcuni
canali con la loro chiave `channels`: saluti, karma, attività programmate, singoli comandi e
risposte, op automatico, log delle chat. Memoria, ban e karma sono tenuti per canale.

### Creare e chiudere canali da IRC

In IRC un canale nasce quando qualcuno ci entra e sparisce quando resta vuoto. Il bot crea due
tipi di canali:

- **permanenti**, per le nuove esigenze della community (un canale per un linguaggio, un
  progetto, un gruppo di lavoro): `!crea #rust`. Restano finché non li chiudi con `!chiudi`;
- **temporanei**, per un evento (un hackathon, una live, un Linux Day): `!evento #hackathon 3d`
  oppure `!evento #linuxday 26/10 19:00`. Il bot avvisa nel canale prima della scadenza
  (`warn_before`, default un'ora), alla scadenza saluta, toglie la registrazione da ChanServ ed
  esce; se il PC era spento alla scadenza lo fa appena riparte. `!proroga #hackathon 1d` sposta
  la scadenza, `!proroga #hackathon perm` lo rende permanente (se l'evento diventa una cosa fissa).

Con i comandi della config d'esempio (solo admin):

```
<fabio> !crea #rust
-TheLinuxBot- Creo #rust con il modello progetto: entro, lo registro su ChanServ, imposto topic,
              modi e descrizione del modello. Lo ricorderò anche dopo un riavvio.
<fabio> !canali
-TheLinuxBot- #linux (dal file): 42 utenti, op ✓, topic ✓, modi ✓
-TheLinuxBot- #rust (creato da fabio il 01/11/2026, modello progetto, registrato): 1 utenti, op ✓, topic ✓, modi ✓
<fabio> !evento #hackathon 15/11 18:00
-TheLinuxBot- Creo #hackathon con il modello evento: entro, lo registro su ChanServ, imposto topic,
              modi e descrizione del modello. È temporaneo: chiude fra 5 giorni e 6 ore (15/11/2026 18:00);
              per spostarlo !proroga.
  ... il 15/11 alle 17:00, nel canale:
<TheLinuxBot> ⏰ #hackathon è un canale temporaneo: chiude fra 1 ora (15/11/2026 18:00).
  ... alle 18:00:
<TheLinuxBot> #hackathon chiude adesso: grazie a tutti per aver partecipato!
<fabio> !chiudi #rust
-TheLinuxBot- Sicuro? Esco da #rust e tolgo la registrazione da ChanServ. Per confermare scrivi
              !chiudi #rust conferma entro 60 secondi.
```

- **`!crea #canale [modello] [scadenza]`** ed **`!evento #canale <scadenza> [modello]`**: il bot
  entra (se il canale non esisteva ne diventa op), lo registra su ChanServ appena è op e
  identificato, e applica topic, modi, descrizione e owner del modello scelto in
  `channel_templates` (altrimenti `default_template`, o `event_template` per `!evento`).
- **Scadenze**: una durata (`6h`, `3d`, `1w`), una data (`15/11`, `15/11 18:00`,
  `2026-11-15 18:00`) o un'ora (`18:30`). Un modello può avere una durata predefinita
  (`duration: 1d`): i canali creati con quel modello nascono temporanei. Nel topic, `{expires}`
  mostra quando chiude e si aggiorna con `!proroga`.
- **Memoria**: i canali creati stanno nel database e, a ogni avvio o `!reload`, si aggiungono a
  quelli del file. Per il resto del bot sono canali come gli altri.
- **`!chiudi #canale`**: chiede conferma, poi toglie la registrazione da ChanServ (`DROP`; se
  ChanServ chiede un codice di conferma, come fa Atheme, il bot lo manda da solo), esce e
  dimentica il canale. Il canale sparisce quando esce anche l'ultimo utente.
- I canali scritti nel file non si chiudono da IRC: si tolgono dal file e si fa `!reload`.
- `!entra` e `!esci` restano per entrare e uscire al volo: quei canali non vengono ricordati.
- Il bot deve essere identificato con NickServ per registrare i canali; i nomi già registrati
  da altri vengono rifiutati da ChanServ e la risposta finisce nel log.

```yaml
channel_management:
  default_template: progetto
  event_template: evento
  warn_before: 1h
  register: true
  drop: true
  max_channels: 20
channel_templates:
  progetto:
    topic: "{channel} | Canale di progetto | Regole: {rules_url}"
    modes: "+nt"
    owners: ["*!*@user/fabio"]
  evento:
    duration: 1d
    topic: "{channel} | Canale dell'evento, aperto fino al {expires}"
    modes: "+nt"
```

## Attività programmate

La sezione `schedules` esegue azioni a orari fissi (`at`) o a intervalli (`every`), nei canali
indicati (o in tutti quelli in cui è il bot). Gli orari seguono `behavior.timezone`
(es. `Europe/Rome`, ora legale compresa); se manca, quello del sistema.

```yaml
behavior:
  timezone: Europe/Rome

schedules:
  - name: karma-mezzanotte
    at: "00:00"
    channels: ["#linux"]
    actions:
      - karma_top:
          size: 10
          skip_empty: true      # niente messaggio se nessuno ha karma
          text: "🏆 Top ten del karma{where} a mezzanotte: {list}"
  - name: regole-del-venerdi
    at: ["09:00", "21:00"]      # anche più orari
    days: [ven]                 # lun mar mer gio ven sab dom (anche per esteso o in inglese)
    actions:
      - say: "Promemoria: le regole sono qui {rules_url}"
  - name: consiglio
    every: 6h                   # 30m, 2h, 1d... (minimo 1m)
    actions:
      - say: ["💡 Ctrl+R cerca nella cronologia", "💡 'cd -' torna alla cartella precedente"]
```

- Qualsiasi azione va bene: `say`, `karma_top`, `topic`, `mode`, `raw`, `db_backup`...; nei
  testi `{channel}` è il canale in cui l'attività sta girando, `{schedule}` il suo nome e
  `{late}` dice se è in ritardo. Con `per_channel: false` le azioni girano una volta sola, senza
  canale (es. un backup).
- `!esegui <nome>` (admin) la esegue subito, per provarla senza aspettare l'orario.
- Il bot controlla le attività ogni minuto. L'ultima esecuzione è salvata nel database, quindi
  un riavvio non le ripete. Se all'orario previsto il bot era spento o non ancora nel canale,
  l'attività viene eseguita appena possibile, purché non sia passato più di `catch_up`
  (default `10m`; es. `catch_up: 12h`); oltre, viene saltata con un avviso nel log.

## Memoria fra un riavvio e l'altro

Il bot può essere riavviato o spento quando vuoi (anche di colpo): tutto ciò che deve ricordare
sta in un database SQLite su disco, `memory.database` (default `linuxbot.db` nella cartella del
file di configurazione, qualunque sia la cartella da cui lo avvii).

| Cosa | Dopo un riavvio |
|---|---|
| karma, classifica, cooldown e limite giornaliero del karma | resta |
| ban, con chi li ha messi, motivi e scadenze dei ban a tempo | resta (i ban scaduti mentre era spento vengono tolti appena riparte) |
| chi è passato (seen), messaggi lasciati e non ancora consegnati | resta |
| ultima esecuzione delle attività programmate | resta: non vengono ripetute, e quelle saltate si recuperano (`catch_up`) |
| ID dei canali YouTube già risolti | resta (niente quota sprecata) |
| attese fra un uso e l'altro dei comandi, cache delle ricerche, `!stats` | si azzerano |

- **Spegnimenti improvvisi**: ogni modifica viene scritta subito su disco (SQLite in modalità
  WAL con `synchronous=FULL`), quindi anche un `kill -9` o un PC che si spegne non fanno
  perdere i dati già confermati.
- **Una sola copia**: su Linux e macOS il bot blocca il database (`<database>.lock`); una
  seconda copia avviata per sbaglio si ferma con un errore invece di scrivere sugli stessi dati.
  Il blocco si libera da solo quando il bot termina, in qualunque modo.
- **Attività saltate**: se il PC era spento all'orario di un'attività, `catch_up` dice per quanto
  tempo recuperarla appena il bot riparte (default 10 minuti). Nella config d'esempio la top ten
  di mezzanotte ha `catch_up: 12h`: riacceso alle 8, il bot la pubblica con "(prevista alle 00:00)".
- **Backup**: l'azione `db_backup` fa una copia coerente del database (anche mentre il bot
  scrive) in una cartella, tenendo le ultime `keep`. Nella config d'esempio c'è un backup ogni
  notte alle 4 (recuperato entro un giorno se il PC era spento) e il comando `!backup` per gli
  admin. Per ripristinare: ferma il bot e copia il backup al posto del database.
- All'avvio il log dice cosa ha ritrovato, ad esempio
  `Database /home/fabio/bot/linuxbot.db: utenti visti: 152, messaggi in attesa: 2, ban attivi: 3, utenti con karma: 40`.

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

## Avvio automatico come servizio

Lo script [`contrib/install-service.sh`](../contrib/install-service.sh) installa il bot come
servizio systemd, che parte da solo all'accensione e si riavvia se cade. Può essere rilanciato
quando vuoi (ad esempio dopo un `git pull`): non sovrascrive configurazione, password e dati.

### Servizio utente (consigliato su un PC personale)

```bash
git clone https://github.com/the-linux-nerd/the-linux-bot && cd the-linux-bot
contrib/install-service.sh
```

- Gira con il tuo utente; il programma resta nella cartella clonata (in un ambiente Python
  `.venv` creato dallo script).
- Configurazione in `~/.config/linuxbot/config.yaml`, password e chiavi in
  `~/.config/linuxbot/linuxbot.env` (leggibile solo da te), dati, backup e log in
  `~/.local/share/linuxbot`.
- Abilita `loginctl enable-linger`, così il bot parte **all'accensione del computer anche
  senza fare login** (se non ha i permessi, lo script dice il comando da lanciare con sudo).

```bash
systemctl --user status linuxbot      # stato
journalctl --user -u linuxbot -f      # log in tempo reale
systemctl --user reload linuxbot      # ricarica la configurazione senza disconnettere
systemctl --user restart linuxbot     # riavvia (dopo un git pull, o se cambi server/identity)
```

### Servizio di sistema

```bash
contrib/install-service.sh --system   # chiede sudo
```

- Utente dedicato `linuxbot` senza login; programma in `/opt/the-linux-bot` (di root, non
  modificabile dal servizio), configurazione in `/etc/linuxbot/config.yaml` (leggibile ma non
  scrivibile dal bot), password in `/etc/linuxbot/linuxbot.env` (solo root), dati in
  `/var/lib/linuxbot`.
- Il servizio ha le protezioni di systemd attive (`ProtectSystem=strict`, `ProtectHome`,
  `NoNewPrivileges`, nessuna capability...): può scrivere solo nella sua cartella dati.
- Per aggiornare: `git pull` e di nuovo `contrib/install-service.sh --system`.

```bash
systemctl status linuxbot
journalctl -u linuxbot -f             # -p err: solo gli errori
sudo systemctl reload linuxbot
```

### In entrambi i casi

- **Dati esistenti**: se nella cartella clonata ci sono già `config.yaml` e `linuxbot.db`
  (perché avevi avviato il bot a mano), lo script li copia nel posto nuovo: karma, ban e memoria
  non si perdono. Se nel posto nuovo c'è già un database, non lo tocca.
- **Password**: scrivile nel file `linuxbot.env` (`LINUXBOT_NICKSERV_PASSWORD=...`,
  `YOUTUBE_API_KEY=...`); nella configurazione si richiamano con `${NOME}`.
- **Controllo**: prima di avviare il servizio lo script valida la configurazione; se ci sono
  errori lo abilita ma non lo avvia, e dice cosa correggere.
- **Riavvii**: se il bot cade, systemd lo riavvia dopo 10 secondi; non insiste se la
  configurazione è sbagliata o se il database è già usato da un'altra copia.
- **Log**: con systemd i messaggi vanno nel journal con il livello corretto (`journalctl -p err`
  mostra solo gli errori); il file di log, se configurato, va nella cartella dati.
- `--uninstall` rimuove il servizio (configurazione e dati restano); `--dry-run` mostra cosa
  farebbe lo script senza toccare niente.
- I file di servizio sono in [`contrib/systemd/`](../contrib/systemd/), se preferisci installarli
  a mano. Le modifiche a `server` e `identity` richiedono un riavvio, il resto basta un `reload`.
- Da riga di comando, `--data-dir <cartella>` (o `LINUXBOT_DATA_DIR`) sposta database, log e
  backup con percorsi relativi in quella cartella invece che accanto alla configurazione.
