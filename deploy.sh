#!/bin/bash
# Atualiza o ARPGOV no VPS após git push no GitHub.
# Uso no servidor: /var/www/arpgov/deploy.sh
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="${ARPGOV_SERVICE_NAME:-arpgov}"
BRANCH="${ARPGOV_DEPLOY_BRANCH:-main}"

cd "$APP_DIR"

echo ">>> Atualizando código ($BRANCH)..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git log -1 --oneline

echo ">>> Dependências Python..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

mkdir -p instance
mkdir -p static/uploads/catalog static/uploads/catalog_attachments \
  static/uploads/ata_company_docs static/uploads/lead_chat \
  static/uploads/portal_clients static/uploads/finance_company \
  static/uploads/finance_rep static/uploads/lead_pipeline

echo ">>> Reiniciando $SERVICE_NAME..."
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart "$SERVICE_NAME"
  sudo systemctl status "$SERVICE_NAME" --no-pager || true
else
  echo "systemctl não encontrado — reinicie o Gunicorn manualmente."
fi

echo ">>> Deploy concluído."
