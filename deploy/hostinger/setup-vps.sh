#!/bin/bash
# Instalação inicial do ARPGOV no VPS Hostinger KVM4 (Ubuntu/Debian).
#
# Uso (como root no VPS):
#   curl -fsSL https://raw.githubusercontent.com/arpsistemaslv-cloud/ARPGOV/main/deploy/hostinger/setup-vps.sh | bash -s -- \
#     --domain arpgov.seudominio.com.br \
#     --email admin@seudominio.com.br
#
# Ou, após git clone:
#   sudo bash deploy/hostinger/setup-vps.sh --domain arpgov.seudominio.com.br --email admin@seudominio.com.br
#
set -euo pipefail

DOMAIN=""
EMAIL=""
REPO="https://github.com/arpsistemaslv-cloud/ARPGOV.git"
APP_DIR="/var/www/arpgov"
DEPLOY_USER="deploy"
GUNICORN_PORT="8001"
BRANCH="main"
SKIP_SSL="0"
SKIP_CLONE="0"

usage() {
  echo "Uso: $0 --domain DOMINIO --email EMAIL_SSL [opções]"
  echo ""
  echo "Opções:"
  echo "  --domain DOMINIO     Domínio do site (registro A → IP do VPS)"
  echo "  --email EMAIL        E-mail para Let's Encrypt (Certbot)"
  echo "  --repo URL           Repositório Git (padrão: $REPO)"
  echo "  --app-dir PATH       Pasta do app (padrão: $APP_DIR)"
  echo "  --branch BRANCH      Branch Git (padrão: main)"
  echo "  --skip-ssl           Não rodar Certbot (só HTTP)"
  echo "  --skip-clone         App já clonado em --app-dir"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --skip-ssl) SKIP_SSL="1"; shift ;;
    --skip-clone) SKIP_CLONE="1"; shift ;;
    -h|--help) usage ;;
    *) echo "Opção desconhecida: $1"; usage ;;
  esac
done

[[ -n "$DOMAIN" ]] || usage
[[ -n "$EMAIL" ]] || usage

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Execute como root: sudo bash $0 ..."
  exit 1
fi

echo "=== ARPGOV — setup Hostinger KVM4 ==="
echo "Domínio: $DOMAIN"
echo "App:     $APP_DIR"
echo ""

echo ">>> Pacotes do sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git ufw curl

echo ">>> Usuário deploy..."
if ! id "$DEPLOY_USER" &>/dev/null; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG www-data "$DEPLOY_USER" 2>/dev/null || true

echo ">>> Firewall..."
ufw allow OpenSSH 2>/dev/null || true
ufw allow 'Nginx Full' 2>/dev/null || true
echo "y" | ufw enable 2>/dev/null || true

echo ">>> Código do GitHub..."
mkdir -p "$(dirname "$APP_DIR")"
chown "$DEPLOY_USER:$DEPLOY_USER" "$(dirname "$APP_DIR")"
if [[ "$SKIP_CLONE" == "1" ]]; then
  echo "Pulando clone (--skip-clone)."
elif [[ -d "$APP_DIR/.git" ]]; then
  echo "Repositório já existe em $APP_DIR"
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" fetch origin "$BRANCH"
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" checkout "$BRANCH"
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" pull origin "$BRANCH"
else
  sudo -u "$DEPLOY_USER" git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

echo ">>> Python venv..."
sudo -u "$DEPLOY_USER" bash -c "
  cd '$APP_DIR'
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip -q
  pip install -r requirements.txt -q
"

echo ">>> Pastas de dados..."
sudo -u "$DEPLOY_USER" mkdir -p "$APP_DIR/instance"
sudo -u "$DEPLOY_USER" mkdir -p \
  "$APP_DIR/static/uploads/catalog" \
  "$APP_DIR/static/uploads/catalog_attachments" \
  "$APP_DIR/static/uploads/ata_company_docs" \
  "$APP_DIR/static/uploads/lead_chat" \
  "$APP_DIR/static/uploads/portal_clients" \
  "$APP_DIR/static/uploads/finance_company" \
  "$APP_DIR/static/uploads/finance_rep" \
  "$APP_DIR/static/uploads/lead_pipeline"

echo ">>> Arquivo .env..."
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/hostinger/env.production.example" "$APP_DIR/.env"
  sed -i "s|ARPGOV_DOMAIN|$DOMAIN|g" "$APP_DIR/.env"
  SECRET=$(openssl rand -hex 32)
  sed -i "s|GERE_UMA_CHAVE_LONGA_ALEATORIA_AQUI|$SECRET|g" "$APP_DIR/.env"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo ""
  echo "*** IMPORTANTE: edite $APP_DIR/.env e defina PAINEL_ADMIN_PASSWORD e CRM_ADMIN_PASSWORD ***"
  echo "    nano $APP_DIR/.env"
  echo ""
else
  echo ".env já existe — mantido."
fi

echo ">>> systemd (arpgov)..."
cp "$APP_DIR/deploy/hostinger/arpgov.service" /etc/systemd/system/arpgov.service
systemctl daemon-reload
systemctl enable arpgov
systemctl restart arpgov

echo ">>> sudoers (deploy pode reiniciar o serviço)..."
cp "$APP_DIR/deploy/hostinger/sudoers-deploy" /etc/sudoers.d/arpgov-deploy
chmod 440 /etc/sudoers.d/arpgov-deploy
visudo -c

echo ">>> Nginx..."
sed "s/ARPGOV_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/hostinger/nginx-arpgov.conf" \
  > /etc/nginx/sites-available/arpgov
ln -sf /etc/nginx/sites-available/arpgov /etc/nginx/sites-enabled/arpgov
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t
systemctl reload nginx

chmod +x "$APP_DIR/deploy.sh"

if [[ "$SKIP_SSL" == "0" ]]; then
  echo ">>> HTTPS (Let's Encrypt)..."
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || {
    echo "Certbot falhou. Verifique se o DNS ($DOMAIN) aponta para este servidor."
    echo "Depois rode: certbot --nginx -d $DOMAIN"
  }
fi

echo ""
echo "=== Setup concluído ==="
echo ""
echo "Site:     http://$DOMAIN  (HTTPS após Certbot)"
echo "Painel:   https://$DOMAIN/admin"
echo "CRM:      https://$DOMAIN/crm"
echo "Comercial: https://$DOMAIN/comercial/entrar"
echo ""
echo "Próximos passos:"
echo "  1. nano $APP_DIR/.env  (senhas do painel e CRM)"
echo "  2. systemctl restart arpgov"
echo "  3. Copiar banco e uploads do PC (primeira vez):"
echo "     scp instance/portal.db deploy@SEU_IP:$APP_DIR/instance/"
echo "     scp -r static/uploads deploy@SEU_IP:$APP_DIR/static/"
echo "  4. Atualizações futuras: git push → ssh deploy@IP $APP_DIR/deploy.sh"
echo ""
