# Guida alla configurazione

Dalla prima installazione al bot che parte da solo all'accensione del computer. Ogni passo
dice cosa fare e come controllare che abbia funzionato. Per l'elenco completo delle opzioni vedi
il [riferimento](riferimento.md), per come funziona ogni parte le [funzioni](funzioni.md).

1. [Cosa serve](#1-cosa-serve)
2. [Registra il nick del bot](#2-registra-il-nick-del-bot)
3. [Dai al bot l'accesso ai canali](#3-dai-al-bot-laccesso-ai-canali)
4. [Scarica il bot](#4-scarica-il-bot)
5. [Scrivi la configurazione minima](#5-scrivi-la-configurazione-minima)
6. [Provalo a mano](#6-provalo-a-mano)
7. [Installalo come servizio](#7-installalo-come-servizio)
8. [Aggiungi le funzioni che ti servono](#8-aggiungi-le-funzioni-che-ti-servono)
9. [Modificare la configurazione](#9-modificare-la-configurazione)
10. [Aggiornamenti e backup](#10-aggiornamenti-e-backup)
11. [Se qualcosa non va](#11-se-qualcosa-non-va)

## 1. Cosa serve

- Un computer Linux (o un server) con **Python 3.10 o successivo** e **git**. Per l'avvio
  automatico serve **systemd** (Debian, Ubuntu, Fedora, Arch... ce l'hanno).
- Un client IRC con cui sei **founder** (o hai i permessi di gestione) dei canali del bot.
- Facoltativo, per la ricerca: una chiave Stack Exchange e/o una chiave YouTube Data API
  (vedi [Ricerca](funzioni.md#ricerca-di-risposte-tecniche)).

Non serve sapere che software usa il server (Anope, Atheme...): il bot lo scopre da solo.

## 2. Registra il nick del bot

Dal tuo client IRC, prendi temporaneamente il nick del bot e registralo:

```
/nick TheLinuxBot
/msg NickServ REGISTER una-password-robusta tua@email.it
```

Se il server lo chiede, conferma la registrazione con il codice che arriva per email. Poi
torna al tuo nick (`/nick tuonick`). Annota la password: servirà al bot.

## 3. Dai al bot l'accesso ai canali

Perché ChanServ dia l'op al bot appena entra, il bot deve avere l'auto-op nel canale. Da
founder del canale:

| Services | Comando |
|---|---|
| Anope | `/msg ChanServ AOP #linux ADD TheLinuxBot` |
| Atheme | `/msg ChanServ FLAGS #linux TheLinuxBot +Oo` |

Se non sai quali services ci sono, prova il primo: se ChanServ risponde "comando sconosciuto",
usa il secondo. Per cambiare la **descrizione** del canale (solo Anope) il bot deve avere su
ChanServ il permesso SET, di solito riservato al founder o ai livelli più alti. I canali creati
con `!crea` non hanno bisogno di niente: il bot li registra e ne diventa founder.

## 4. Scarica il bot

```bash
git clone https://github.com/the-linux-nerd/the-linux-bot
cd the-linux-bot
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 5. Scrivi la configurazione minima

Parti dalla configurazione minima, non da quella d'esempio completa (che contiene tutto):

```bash
cp config.minimal.yaml config.yaml
```

Apri `config.yaml` e cambia:

| Dove | Cosa mettere |
|---|---|
| `server.host`, `server.port`, `server.tls` | indirizzo del server; 6697 con TLS (consigliato) o 6667 senza |
| `identity.nick` | il nick registrato al passo 2 |
| `channels` | i tuoi canali, es. `- name: "#linux"` |
| `admins` | la tua maschera: con `/whois tuonick` vedi `tuonick!user@host`; usa `*!*@host` (meglio se è un cloak, es. `*!*@user/fabio`) |

**La password non va scritta nel file.** Nel file c'è `${LINUXBOT_NICKSERV_PASSWORD}`: il bot
la prende dalla variabile d'ambiente con quel nome. Per la prova a mano:

```bash
export LINUXBOT_NICKSERV_PASSWORD='una-password-robusta'
```

Controlla il file:

```bash
.venv/bin/python -m linuxbot -c config.yaml --check
```

Deve rispondere `Configurazione valida: ...`. Altrimenti dice esattamente quale riga
correggere (ad esempio una chiave scritta male o una variabile d'ambiente mancante).

## 6. Provalo a mano

```bash
.venv/bin/python -m linuxbot -c config.yaml -v
```

Con `-v` vedi anche tutto il traffico IRC. Nei primi secondi dovresti vedere, in quest'ordine:

```
[connection] Connesso a irc.example.org:6697
[auth] Autenticazione SASL riuscita            (oppure, senza SASL: NickServ: Password accepted ...)
[auth] Services: atheme (atheme-7.2.12)
[auth] Identificato con i services
[channels] Entrato in #linux
[op] Ho ricevuto l'op in #linux da ChanServ
```

In canale scrivi `!ping` e `!help`: il bot deve rispondere. Ferma con `Ctrl+C`.

Se qualcosa non va, vedi [Se qualcosa non va](#11-se-qualcosa-non-va).

## 7. Installalo come servizio

```bash
contrib/install-service.sh
```

Lo script copia la tua `config.yaml` in `~/.config/linuxbot/` e prepara
`~/.config/linuxbot/linuxbot.env`. Scrivi la password lì dentro:

```
LINUXBOT_NICKSERV_PASSWORD=una-password-robusta
```

poi riavvia il servizio:

```bash
systemctl --user restart linuxbot
journalctl --user -u linuxbot -f        # i log; Ctrl+C per uscire
```

Il bot ora parte da solo all'accensione del computer, anche senza fare login. Da qui in poi la
configurazione da modificare è `~/.config/linuxbot/config.yaml`. Dettagli e servizio di sistema
in [Avvio automatico come servizio](funzioni.md#avvio-automatico-come-servizio).

## 8. Aggiungi le funzioni che ti servono

Aggiungine una alla volta: copia la sezione da [`config.example.yaml`](../config.example.yaml)
(che le contiene tutte, commentate), adattala, controlla con `--check` e ricarica (passo 9).

| Funzione | Sezioni da copiare | Spiegazione |
|---|---|---|
| Altri comandi (`!regole`, `!man`, link rapidi) | voci di `commands`, `variables` | [Comandi](funzioni.md#comandi-risposte-e-azioni), [Link rapidi](funzioni.md#link-rapidi) |
| Risposte automatiche ("che distro mi consigli?") | `responses` | [Comandi, risposte e azioni](funzioni.md#comandi-risposte-e-azioni) |
| Saluti con regole (auto-voice, auto-op) | `greetings.rules` | [Il saluto](funzioni.md#il-saluto-e-la-) |
| "Chi è passato", messaggi lasciati | `memory`, comandi `seen` `passati` `lascia` `messaggi`, risposte "memoria" | [Memoria](funzioni.md#memoria-seen-chi-è-passato-messaggi-lasciati) |
| Karma e top ten di mezzanotte | `karma`, comandi `karma` `top`, `schedules` | [Karma](funzioni.md#karma) |
| Ban, ban a tempo, owner immuni | `bans`, `channels[].owners`, comandi `ban` `tempban` `unban` `banlist` `baninfo` | [Ban](funzioni.md#ban) |
| Topic, modi, descrizione dei canali | `channels[].topic`, `modes`, `description` | [Topic, modi e descrizione](funzioni.md#topic-modi-e-descrizione-del-canale) |
| Creare canali da IRC, permanenti o per un evento | `channel_management`, `channel_templates`, comandi `crea` `evento` `proroga` `chiudi` `canali` | [Canali](funzioni.md#canali-multipli-e-canali-creati-da-irc) |
| Ricerca su Stack Overflow e YouTube | `search`, comandi `cerca` `video`, risposte "ricerca" | [Ricerca](funzioni.md#ricerca-di-risposte-tecniche) |
| Il bot che si presenta ("chi sei?") | `about`, comando `info`, risposte "chi-sei" | [Il bot si spiega](funzioni.md#il-bot-si-spiega-agli-utenti) |
| Attività a orari fissi, backup notturno | `schedules`, `behavior.timezone` | [Attività programmate](funzioni.md#attività-programmate) |
| Log su file, avvisi su IRC, `!stats` | `logging`, comando `stats` | [Log](funzioni.md#log-debug-e-statistiche) |

Le funzioni che usano il database (memoria, karma, ban, canali creati) lo creano da sole al
primo avvio: con il servizio utente sta in `~/.local/share/linuxbot/linuxbot.db`.

## 9. Modificare la configurazione

1. Modifica il file (`~/.config/linuxbot/config.yaml` se usi il servizio).
2. Controlla (la prima riga carica le password da `linuxbot.env`, che il controllo richiede):

   ```bash
   set -a; . ~/.config/linuxbot/linuxbot.env; set +a
   .venv/bin/python -m linuxbot -c ~/.config/linuxbot/config.yaml --check
   ```

3. Applica senza disconnettere il bot: `systemctl --user reload linuxbot`, oppure scrivi
   `!reload` in IRC (se sei admin). Se il file ha errori il bot tiene la configurazione
   precedente: con `!reload` te lo dice in risposta, con `systemctl reload` lo scrive nel log.

Le modifiche a `server` e `identity` richiedono un riavvio (`systemctl --user restart linuxbot`).

## 10. Aggiornamenti e backup

- **Aggiornare**: nella cartella del bot `git pull`, poi `systemctl --user restart linuxbot`
  (con il servizio di sistema: di nuovo `contrib/install-service.sh --system`).
- **Backup**: la config d'esempio ne fa uno ogni notte (attività `backup-database`) in
  `~/.local/share/linuxbot/backups/`; `!backup` (admin) ne fa uno subito.
- **Ripristino**: ferma il bot (`systemctl --user stop linuxbot`), copia il backup al posto di
  `~/.local/share/linuxbot/linuxbot.db` e riavvialo.

## 11. Se qualcosa non va

Guarda prima i log: `journalctl --user -u linuxbot -n 100` (gli errori soli: aggiungi `-p err`).
Per vedere anche il traffico IRC, in `logging.levels` metti `irc: DEBUG` e ricarica.

| Cosa succede | Causa probabile | Soluzione |
|---|---|---|
| `Errore di configurazione: variabile d'ambiente non definita` | la password non è impostata | mettila in `linuxbot.env` (servizio) o `export` (a mano) |
| `Connessione persa: Name or service not known` | indirizzo del server sbagliato o niente rete | controlla `server.host`; il bot riprova da solo |
| `Connessione persa: ... certificate verify failed` | certificato non valido | controlla `server.port`/`tls`; solo se il server usa un certificato self-signed metti `tls_verify: false` |
| `Identificazione con NickServ fallita` | password sbagliata, o nick non registrato | rifai il passo 2; controlla la password in `linuxbot.env` |
| `Nessuna conferma da NickServ dopo 15 secondi` | i services rispondono con frasi diverse | non è grave; se l'identificazione funziona, aggiungi la frase a `auth.nickserv.success_pattern` |
| `Non ho ottenuto l'op in #linux` | il bot non ha l'auto-op su ChanServ | rifai il passo 3 |
| Il bot usa il nick `TheLinuxBot_` | il nick era occupato (vecchia connessione) | di solito se lo riprende da solo; nel log trovi `[auth]` con cosa ha provato |
| Il bot non risponde ai comandi | prefisso diverso, utente in `behavior.ignore`, permessi | prova `!help`; i comandi da op/admin rispondono "non hai i permessi" |
| `il database ... è già in uso da un'altra copia` | il bot è già acceso (es. come servizio) | fermalo prima di avviarlo a mano: `systemctl --user stop linuxbot` |
| La ricerca risponde "non è disponibile" | il computer non raggiunge le API, o quota finita | nel log `[search]` trovi il motivo; una chiave Stack Exchange/YouTube alza i limiti |
| Il topic o i modi non vengono impostati | il bot non è op, o la lettera non esiste su quel server | `!canali` mostra lo stato; il log `[channels]` dice cosa è stato rifiutato |
| Il servizio non parte dopo il riavvio del PC | manca "linger" | `sudo loginctl enable-linger $USER` |

Per segnalare un problema allega le righe di log intorno all'errore: le password non ci sono,
il bot le nasconde.
