#!/usr/bin/env bash
# Installa The Linux Bot come servizio systemd, avviato automaticamente all'accensione.
#
#   contrib/install-service.sh            servizio utente (consigliato su un PC personale)
#   contrib/install-service.sh --system   servizio di sistema (utente dedicato "linuxbot", serve sudo)
#   ... --uninstall                       rimuove il servizio (configurazione e dati restano)
#   ... --dry-run                         mostra cosa farebbe senza toccare niente
#
# Servizio utente:  programma = questa cartella, configurazione in ~/.config/linuxbot,
#                   dati (database, backup, log) in ~/.local/share/linuxbot.
# Servizio sistema: programma in /opt/the-linux-bot, configurazione in /etc/linuxbot,
#                   dati in /var/lib/linuxbot.
# Se in questa cartella ci sono già config.yaml e linuxbot.db (bot avviato a mano), vengono
# copiati nel posto nuovo, così karma, ban e memoria non si perdono.

set -euo pipefail

ORIG_ARGS=("$@")
MODE=user
ACTION=install
DRY=0
SERVICE=linuxbot

for arg in "$@"; do
    case "$arg" in
        --user) MODE=user ;;
        --system) MODE=system ;;
        --uninstall) ACTION=uninstall ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opzione sconosciuta: $arg (usa --help)" >&2; exit 2 ;;
    esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mErrore:\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = 1 ]; then printf '   + %s\n' "$*"; else "$@"; fi; }

# scrive un file (o, in dry-run, dice soltanto dove lo scriverebbe)
write_file() {  # write_file <percorso> <modo> ; contenuto da stdin
    local path="$1" mode="$2"
    if [ "$DRY" = 1 ]; then printf '   + scrivo %s (permessi %s)\n' "$path" "$mode"; cat >/dev/null; return; fi
    install -m "$mode" /dev/stdin "$path"
}

render_unit() {  # render_unit <template> <app_dir> <config> <data_dir> <env_file>
    sed -e "s|@APP_DIR@|$2|g" -e "s|@CONFIG@|$3|g" -e "s|@DATA_DIR@|$4|g" -e "s|@ENV_FILE@|$5|g" "$1"
}

env_template() {
    cat <<'EOF'
# Password e chiavi del bot, lette dal servizio all'avvio (una per riga, NOME=valore).
# Nel file di configurazione si richiamano con ${NOME}. Questo file è leggibile solo da te/root.
LINUXBOT_NICKSERV_PASSWORD=
LINUXBOT_OPER_PASSWORD=
# LINUXBOT_SASL_PASSWORD=
# LINUXBOT_STAFF_KEY=
# STACKEXCHANGE_KEY=
# YOUTUBE_API_KEY=
EOF
}

# copia coerente di un database SQLite (anche se ha file -wal), solo se la destinazione non esiste
migrate_db() {  # migrate_db <python> <sorgente> <destinazione>
    local py="$1" src="$2" dst="$3"
    [ -f "$src" ] || return 0
    if [ -e "$dst" ]; then
        warn "$dst esiste già: non lo sovrascrivo con $src"
        return 0
    fi
    say "Copio i dati esistenti (karma, ban, memoria): $src -> $dst"
    run "$py" -c 'import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()' "$src" "$dst"
}

setup_venv() {  # setup_venv <app_dir> [editable]
    local app="$1" editable="${2:-}"
    command -v python3 >/dev/null || die "serve python3 (3.10 o successivo)"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "serve Python 3.10 o successivo"
    if [ ! -x "$app/.venv/bin/python" ]; then
        say "Creo l'ambiente Python in $app/.venv"
        run python3 -m venv "$app/.venv"
    fi
    say "Installo il bot e le dipendenze"
    run "$app/.venv/bin/pip" install --quiet --upgrade pip
    if [ -n "$editable" ]; then
        run "$app/.venv/bin/pip" install --quiet -e "$app"   # dopo un git pull basta riavviare
    else
        run "$app/.venv/bin/pip" install --quiet "$app"
    fi
}

check_config() {  # check_config <app_dir> <config> <data_dir> <env_file> [utente]
    local app="$1" conf="$2" data="$3" envf="$4" user="${5:-}"
    say "Controllo la configurazione"
    [ "$DRY" = 1 ] && { printf '   + %s/.venv/bin/linuxbot -c %s --data-dir %s --check\n' "$app" "$conf" "$data"; return 0; }
    local cmd=(env)
    if [ -r "$envf" ]; then
        while IFS= read -r line; do
            case "$line" in ''|\#*) continue ;; *=*) cmd+=("$line") ;; esac
        done < "$envf"
    fi
    cmd+=("$app/.venv/bin/linuxbot" -c "$conf" --data-dir "$data" --check)
    if [ -n "$user" ]; then cmd=(runuser -u "$user" -- "${cmd[@]}"); fi
    if ! "${cmd[@]}"; then
        warn "La configurazione ha errori: correggi $conf e poi riavvia il servizio."
        return 1
    fi
}

# ---------------------------------------------------------------------------- servizio utente

user_install() {
    command -v systemctl >/dev/null || die "systemd non trovato: questo script serve per distribuzioni con systemd"
    [ "$(id -u)" != 0 ] || die "per il servizio utente non usare sudo (oppure usa --system)"
    local conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/linuxbot"
    local data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/linuxbot"
    local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    local conf="$conf_dir/config.yaml" envf="$conf_dir/linuxbot.env"

    setup_venv "$REPO" editable
    run mkdir -p "$conf_dir" "$data_dir" "$unit_dir"
    if [ ! -e "$conf" ]; then
        if [ -f "$REPO/config.yaml" ]; then
            say "Uso la tua configurazione esistente: $REPO/config.yaml -> $conf"
            run cp "$REPO/config.yaml" "$conf"
        else
            say "Creo $conf dalla configurazione minima (da personalizzare: vedi docs/configurazione.md)"
            run cp "$REPO/config.minimal.yaml" "$conf"
        fi
        run chmod 600 "$conf"
    fi
    if [ ! -e "$envf" ]; then
        say "Creo $envf per password e chiavi"
        env_template | write_file "$envf" 600
    fi
    migrate_db "$REPO/.venv/bin/python" "$REPO/linuxbot.db" "$data_dir/linuxbot.db"

    say "Installo il servizio $unit_dir/$SERVICE.service"
    render_unit "$REPO/contrib/systemd/linuxbot-user.service" "$REPO" "$conf" "$data_dir" "$envf" \
        | write_file "$unit_dir/$SERVICE.service" 644
    run systemctl --user daemon-reload

    local ok=1
    check_config "$REPO" "$conf" "$data_dir" "$envf" || ok=0
    if [ "$ok" = 1 ]; then
        run systemctl --user enable --now "$SERVICE"
    else
        run systemctl --user enable "$SERVICE"
    fi

    # senza "linger" i servizi utente partono solo dopo il login: con linger partono all'accensione
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != yes ]; then
        say "Abilito l'avvio all'accensione anche senza login (loginctl enable-linger)"
        if ! run loginctl enable-linger "$USER" 2>/dev/null; then
            warn "Non ho potuto abilitare linger: esegui 'sudo loginctl enable-linger $USER'"
        fi
    fi

    cat <<EOF

Fatto. Il bot parte da solo all'accensione del computer.
  Configurazione:  $conf
  Password/chiavi: $envf
  Dati e backup:   $data_dir
Comandi utili:
  systemctl --user status $SERVICE        stato
  journalctl --user -u $SERVICE -f        log in tempo reale
  systemctl --user reload $SERVICE        ricarica la configurazione senza disconnettere
  systemctl --user restart $SERVICE       riavvia
  systemctl --user stop $SERVICE          ferma (riparte al prossimo avvio del PC)
EOF
    [ "$ok" = 1 ] || echo "  -> Prima correggi la configurazione, poi: systemctl --user start $SERVICE"
}

user_uninstall() {
    local unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE.service"
    run systemctl --user disable --now "$SERVICE" || true
    run rm -f "$unit"
    run systemctl --user daemon-reload
    say "Servizio rimosso. Configurazione e dati restano in ~/.config/linuxbot e ~/.local/share/linuxbot."
}

# ---------------------------------------------------------------------------- servizio di sistema

system_install() {
    command -v systemctl >/dev/null || die "systemd non trovato: questo script serve per distribuzioni con systemd"
    if [ "$(id -u)" != 0 ] && [ "$DRY" = 0 ]; then
        exec sudo "$0" "${ORIG_ARGS[@]}"
    fi
    local app=/opt/the-linux-bot conf_dir=/etc/linuxbot data_dir=/var/lib/linuxbot
    local conf="$conf_dir/config.yaml" envf="$conf_dir/linuxbot.env"
    local unit="/etc/systemd/system/$SERVICE.service"

    if ! id linuxbot >/dev/null 2>&1; then
        say "Creo l'utente di sistema linuxbot"
        run useradd --system --user-group --home-dir "$data_dir" --no-create-home \
            --shell "$(command -v nologin || echo /bin/false)" linuxbot
    fi

    say "Copio il programma in $app"
    run mkdir -p "$app"
    if [ "$DRY" = 1 ]; then
        printf '   + copio %s in %s (senza .git, .venv, config.yaml, database, log)\n' "$REPO" "$app"
    else
        tar -C "$REPO" --exclude=.git --exclude=.venv --exclude=config.yaml --exclude='*.db*' \
            --exclude=logs --exclude=backups --exclude='__pycache__' --exclude=build --exclude='*.egg-info' \
            -cf - . | tar -C "$app" -xf -
    fi
    setup_venv "$app"
    run chown -R root:root "$app"

    run mkdir -p "$conf_dir" "$data_dir"
    if [ ! -e "$conf" ]; then
        if [ -f "$REPO/config.yaml" ]; then
            say "Uso la tua configurazione esistente: $REPO/config.yaml -> $conf"
            run cp "$REPO/config.yaml" "$conf"
        else
            say "Creo $conf dalla configurazione minima (da personalizzare: vedi docs/configurazione.md)"
            run cp "$REPO/config.minimal.yaml" "$conf"
        fi
    fi
    run chown root:linuxbot "$conf_dir" "$conf"
    run chmod 750 "$conf_dir"
    run chmod 640 "$conf"
    if [ ! -e "$envf" ]; then
        say "Creo $envf per password e chiavi"
        env_template | write_file "$envf" 600
    fi
    migrate_db "$app/.venv/bin/python" "$REPO/linuxbot.db" "$data_dir/linuxbot.db"
    run chown -R linuxbot:linuxbot "$data_dir"
    run chmod 750 "$data_dir"

    say "Installo il servizio $unit"
    render_unit "$REPO/contrib/systemd/linuxbot.service" "$app" "$conf" "$data_dir" "$envf" | write_file "$unit" 644
    run systemctl daemon-reload

    local ok=1
    check_config "$app" "$conf" "$data_dir" "$envf" linuxbot || ok=0
    if [ "$ok" = 1 ]; then
        run systemctl enable --now "$SERVICE"
    else
        run systemctl enable "$SERVICE"
    fi

    cat <<EOF

Fatto. Il bot parte da solo all'accensione del sistema, come utente "linuxbot".
  Configurazione:  $conf   (modificala con: sudoedit $conf)
  Password/chiavi: $envf
  Dati e backup:   $data_dir
Comandi utili:
  systemctl status $SERVICE          stato
  journalctl -u $SERVICE -f          log in tempo reale (journalctl -u $SERVICE -p err: solo errori)
  sudo systemctl reload $SERVICE     ricarica la configurazione senza disconnettere
  sudo systemctl restart $SERVICE    riavvia
Per aggiornare il bot: git pull in $REPO e poi di nuovo questo script con --system.
EOF
    [ "$ok" = 1 ] || echo "  -> Prima correggi la configurazione, poi: sudo systemctl start $SERVICE"
}

system_uninstall() {
    if [ "$(id -u)" != 0 ] && [ "$DRY" = 0 ]; then
        exec sudo "$0" "${ORIG_ARGS[@]}"
    fi
    run systemctl disable --now "$SERVICE" || true
    run rm -f "/etc/systemd/system/$SERVICE.service"
    run systemctl daemon-reload
    say "Servizio rimosso. Restano: /opt/the-linux-bot, /etc/linuxbot, /var/lib/linuxbot e l'utente linuxbot."
}

case "$MODE/$ACTION" in
    user/install) user_install ;;
    user/uninstall) user_uninstall ;;
    system/install) system_install ;;
    system/uninstall) system_uninstall ;;
esac
