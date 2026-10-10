#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/chekuska-market}"
SERVICE_USER="${SERVICE_USER:-funpay}"

if [[ $(id -u) -ne 0 ]]; then
  echo "Запусти через sudo: sudo bash install-vps.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip sudo

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$PROJECT_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

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

if ! command -v ollama >/dev/null 2>&1; then
  echo "[+] Устанавливаю Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable --now ollama >/dev/null 2>&1 || true

if [[ ! -f .env ]]; then
  cp .env.example .env
fi
chmod 600 .env
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"

cp systemd/chekuska-funpay.service /etc/systemd/system/
cp systemd/chekuska-raise.service /etc/systemd/system/
systemctl daemon-reload

echo
echo "════════════════════════════════════════════"
echo "       Первичная настройка ChekuskaMarket"
echo "════════════════════════════════════════════"
sudo -u "$SERVICE_USER" -H "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/setup.py"

AI_ENABLED="$(grep -E '^AI_ENABLED=' .env | tail -1 | cut -d= -f2- || true)"
OLLAMA_MODEL="$(grep -E '^OLLAMA_MODEL=' .env | tail -1 | cut -d= -f2- || true)"
DRY_RUN="$(grep -E '^DRY_RUN=' .env | tail -1 | cut -d= -f2- || true)"
AUTOPUBLISH_ENABLED="$(grep -E '^AUTOPUBLISH_ENABLED=' .env | tail -1 | cut -d= -f2- || true)"

if [[ "$AI_ENABLED" == "1" && -n "$OLLAMA_MODEL" ]]; then
  echo
  echo "[+] Загружаю модель Ollama: $OLLAMA_MODEL"
  ollama pull "$OLLAMA_MODEL"
fi

echo
echo "[+] Проверяю Python..."
sudo -u "$SERVICE_USER" "$PROJECT_DIR/.venv/bin/python" -m py_compile   "$PROJECT_DIR/bot.py" "$PROJECT_DIR/autoseller.py" "$PROJECT_DIR/setup.py"
sudo -u "$SERVICE_USER" "$PROJECT_DIR/.venv/bin/python" -m unittest -v

if [[ "$DRY_RUN" == "0" ]]; then
  echo
  echo "[+] Запускаю основной FunPay-бот..."
  systemctl enable --now chekuska-funpay
  if [[ "$AUTOPUBLISH_ENABLED" == "1" ]]; then
    echo "[+] Запускаю автономную публикацию..."
    systemctl enable --now chekuska-raise
  else
    systemctl disable --now chekuska-raise >/dev/null 2>&1 || true
  fi

  sleep 2
  echo
  systemctl --no-pager --full status chekuska-funpay | sed -n '1,12p' || true
  if [[ "$AUTOPUBLISH_ENABLED" == "1" ]]; then
    systemctl --no-pager --full status chekuska-raise | sed -n '1,12p' || true
  fi
  echo
  echo "✅ ChekuskaMarket запущен."
  echo "Логи бота:       journalctl -u chekuska-funpay -f"
  echo "Логи публикации: journalctl -u chekuska-raise -f"
else
  systemctl disable --now chekuska-funpay chekuska-raise >/dev/null 2>&1 || true
  echo
  echo "✅ Установка завершена в DRY_RUN=1."
  echo "Тест: sudo -u $SERVICE_USER $PROJECT_DIR/.venv/bin/python $PROJECT_DIR/autoseller.py once"
  echo "Для реального запуска снова выполни:"
  echo "sudo -u $SERVICE_USER $PROJECT_DIR/.venv/bin/python $PROJECT_DIR/setup.py"
  echo "а затем: sudo systemctl enable --now chekuska-funpay chekuska-raise"
fi
