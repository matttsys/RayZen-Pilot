#!/usr/bin/env bash
# RayZen Pilot installer for Linux servers with systemd.
#
#   sudo bash install.sh
#
# Options (environment variables):
#   INSTALL_DIR=/opt/rayzen-pilot   where the bot lives
#   SERVICE_USER=rayzen             system user that runs it
#   BOT_TOKEN=... OWNER_ID=... AI_API_KEY=...   skip the questions (unattended install)
set -euo pipefail

APP="rayzen-pilot"
INSTALL_DIR="${INSTALL_DIR:-/opt/rayzen-pilot}"
SERVICE_USER="${SERVICE_USER:-rayzen}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIN_PY_MINOR=10

if [[ -t 1 ]]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; M=$'\033[35m'; N=$'\033[0m'
else
  B=""; D=""; R=""; G=""; Y=""; C=""; M=""; N=""
fi

STEP=0
TOTAL=7
step() { STEP=$((STEP + 1)); printf "\n%s[%d/%d]%s %s%s%s\n" "$C$B" "$STEP" "$TOTAL" "$N" "$B" "$*" "$N"; }
ok()   { printf "  %s✔%s %s\n" "$G" "$N" "$*"; }
info() { printf "  %s%s%s\n" "$D" "$*" "$N"; }
warn() { printf "  %s!%s %s\n" "$Y" "$N" "$*"; }
die()  { printf "\n  %s✖ %s%s\n\n" "$R" "$*" "$N" >&2; exit 1; }

banner() {
  printf "\n%s" "$M$B"
  cat <<'EOF'
   ___           ____           ___ _ _     _
  | _ \__ _ _  _|_  /___ _ _   | _ (_) |___| |_
  |   / _` | || |/ // -_) ' \  |  _/ | / _ \  _|
  |_|_\__,_|\_, /___\___|_||_| |_| |_|_\___/\__|
            |__/
EOF
  printf "%s  %sTelegram group manager · server install%s\n" "$N" "$D" "$N"
}

[[ "$(id -u)" -eq 0 ]] || die "Run as root:  sudo bash install.sh"
command -v systemctl >/dev/null 2>&1 || die "systemd not found. Use Docker instead (see README)."
[[ -f "$SRC_DIR/app.py" && -d "$SRC_DIR/pilot" ]] || die "Run this from the extracted rayzen-pilot folder."

banner

# ---------------------------------------------------------------- python
step "Python"
py_ok() { "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3, $MIN_PY_MINOR) else 1)" 2>/dev/null; }
find_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && py_ok "$(command -v "$p")"; then command -v "$p"; return 0; fi
  done
  return 1
}
install_python() {
  if command -v apt-get >/dev/null 2>&1; then
    info "installing python3 with apt"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 python3-pip >/dev/null
  elif command -v yum >/dev/null 2>&1; then
    yum install -y -q python3 python3-pip >/dev/null
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm --needed python python-pip >/dev/null
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install python3 python3-pip >/dev/null
  else
    return 1
  fi
}
PY="$(find_python || true)"
if [[ -z "$PY" ]]; then
  install_python || die "Couldn't install Python automatically. Install Python 3.$MIN_PY_MINOR+ and run again."
  PY="$(find_python || true)"
fi
[[ -n "$PY" ]] || die "Python 3.$MIN_PY_MINOR or newer is required (found: $(python3 -V 2>&1 || echo none))."
ok "$("$PY" -V 2>&1) at $PY"

if ! "$PY" -m venv --help >/dev/null 2>&1 || ! "$PY" -c "import ensurepip" >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    ver="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    info "installing python$ver-venv"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "python$ver-venv" >/dev/null 2>&1 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv >/dev/null
  fi
fi

# ---------------------------------------------------------------- user
step "Service user"
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  ok "user $SERVICE_USER exists"
else
  useradd --system --home-dir "$INSTALL_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER" 2>/dev/null \
    || useradd -r -d "$INSTALL_DIR" -s /sbin/nologin "$SERVICE_USER"
  ok "created user $SERVICE_USER"
fi

# ---------------------------------------------------------------- files
step "Files"
mkdir -p "$INSTALL_DIR"
if [[ "$SRC_DIR" != "$(cd "$INSTALL_DIR" && pwd)" ]]; then
  (cd "$SRC_DIR" && tar --exclude='./.env' --exclude='./data' --exclude='./.venv' --exclude='__pycache__' -cf - .) \
    | (cd "$INSTALL_DIR" && tar -xf -)
  ok "copied to $INSTALL_DIR"
else
  ok "installing in place"
fi
mkdir -p "$INSTALL_DIR/data"
[[ -f "$INSTALL_DIR/data/pilot.db" ]] && info "existing database kept"

# ---------------------------------------------------------------- venv
step "Dependencies"
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  "$PY" -m venv "$INSTALL_DIR/.venv" || die "Couldn't create a virtualenv. On Debian/Ubuntu: apt install python3-venv"
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$INSTALL_DIR/.venv/bin/python" -m pip install -q -r "$INSTALL_DIR/requirements.txt" || die "pip install failed (check your internet connection)."
ok "$("$INSTALL_DIR/.venv/bin/python" -c 'import telegram; print("python-telegram-bot", telegram.__version__)')"

# ---------------------------------------------------------------- config
step "Configuration"
needs_config() {
  [[ ! -f "$INSTALL_DIR/.env" ]] && return 0
  grep -qE '^BOT_TOKEN=[0-9]{5,}:' "$INSTALL_DIR/.env" || return 0
  grep -qE '^OWNER_ID=[0-9]+' "$INSTALL_DIR/.env" || return 0
  return 1
}
if [[ -n "${BOT_TOKEN:-}" && -n "${OWNER_ID:-}" ]]; then
  BOT_TOKEN="$BOT_TOKEN" OWNER_ID="$OWNER_ID" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/configure.py" \
    --env "$INSTALL_DIR/.env" --non-interactive >/dev/null
  ok "written from environment variables"
elif needs_config; then
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/configure.py" --env "$INSTALL_DIR/.env" || die "Setup cancelled. Run again when ready."
else
  ok "using existing $INSTALL_DIR/.env  (change it later with: rayzen config)"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"

# ---------------------------------------------------------------- service
step "Service"
sed -e "s#__DIR__#$INSTALL_DIR#g" -e "s#__USER__#$SERVICE_USER#g" \
  "$INSTALL_DIR/deploy/rayzen-pilot.service" > "/etc/systemd/system/$APP.service"
case "$INSTALL_DIR" in /home/*|/root/*) sed -i '/^ProtectHome/d' "/etc/systemd/system/$APP.service" ;; esac
sed -e "s#__DIR__#$INSTALL_DIR#g" -e "s#__USER__#$SERVICE_USER#g" \
  "$INSTALL_DIR/deploy/rayzen" > /usr/local/bin/rayzen
chmod 755 /usr/local/bin/rayzen
systemctl daemon-reload
systemctl enable "$APP" >/dev/null 2>&1
systemctl restart "$APP"
ok "systemd service $APP enabled"
ok "command 'rayzen' installed"

# ---------------------------------------------------------------- check
step "Start"
user=""
for _ in $(seq 1 20); do
  sleep 1
  if journalctl -u "$APP" -n 50 --no-pager 2>/dev/null | grep -q "Application started"; then
    break
  fi
done
if systemctl is-active --quiet "$APP"; then
  user="$(journalctl -u "$APP" -n 80 --no-pager 2>/dev/null | grep -o 'up as @[A-Za-z0-9_]*' | tail -1 | cut -d@ -f2 || true)"
  ok "running${user:+ as @$user}"
else
  warn "service is not running yet. Check: rayzen logs"
fi

cat <<EOF

  ${G}${B}Done.${N}

  ${B}Next${N}
    1. Add ${user:+@$user }to your group and make it admin
    2. Send /settings in the group

  ${B}Manage${N}
    rayzen status     rayzen logs      rayzen restart
    rayzen config     rayzen backup    rayzen update <zip>

EOF
