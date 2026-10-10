#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/chekuska-market}"
SERVICE_USER="${SERVICE_USER:-funpay}"

if [[ $(id -u) -ne 0 ]]; then
  echo "Запусти через sudo: sudo bash install-vps.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3 python3-venv python3-pip

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$PROJECT_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# Script is intended to be run from an already cloned repository.
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "$SOURCE_DIR" != "$PROJECT_DIR" ]]; then
  rm -rf "$PROJECT_DIR"
  git clone https://github.com/ReVerfyx/ChekuskaMarket.git "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

mkdir -p data guides stock vendor
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

if [[ ! -d vendor/FunPayAPI/.git ]]; then
  rm -rf vendor/FunPayAPI
  git clone --depth 1 https://github.com/MorikTV/FunPayAPI.git vendor/FunPayAPI
else
  git -C vendor/FunPayAPI pull --ff-only
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi
chmod 600 .env
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"

cp systemd/chekuska-funpay.service /etc/systemd/system/
cp systemd/chekuska-raise.service /etc/systemd/system/
systemctl daemon-reload

echo
printf 'Установлено: %s\n' "$PROJECT_DIR"
echo "1) nano $PROJECT_DIR/.env"
echo "2) sudo -u $SERVICE_USER $PROJECT_DIR/.venv/bin/python $PROJECT_DIR/bot.py catalog"
echo "3) systemctl enable --now chekuska-funpay"
echo "4) опционально: systemctl enable --now chekuska-raise"
