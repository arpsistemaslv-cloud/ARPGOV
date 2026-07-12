# Deploy ARPGOV — GitHub → VPS Hostinger

## Fluxo

1. Alterações locais → `git push` para o GitHub
2. No VPS: `./deploy.sh` **ou** deploy automático via GitHub Actions

## Primeira vez no PC

```powershell
cd "C:\Users\Victor Hugo\Desktop\PortalGovCRM"
git remote add origin https://github.com/SEU_USUARIO/arpgov-portal.git
git push -u origin main
```

Crie o repositório vazio no GitHub antes do `push`.

## Primeira vez no VPS

```bash
sudo mkdir -p /var/www
sudo chown deploy:deploy /var/www
cd /var/www
git clone https://github.com/SEU_USUARIO/arpgov-portal.git arpgov
cd arpgov
chmod +x deploy.sh

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # preencher senhas e PUBLIC_BASE_URL

# Copiar banco e uploads do PC (só uma vez):
# scp instance/portal.db deploy@IP:/var/www/arpgov/instance/
# scp -r static/uploads deploy@IP:/var/www/arpgov/static/
```

Configure `systemd` (`arpgov.service`) e Nginx conforme documentação do projeto.

## Atualizações

```powershell
git add .
git commit -m "sua mensagem"
git push origin main
```

No VPS (manual):

```bash
/var/www/arpgov/deploy.sh
```

## GitHub Actions (opcional)

No repositório GitHub → **Settings → Secrets and variables → Actions**, crie:

| Secret | Exemplo |
|--------|---------|
| `VPS_HOST` | `123.45.67.89` |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | conteúdo da chave privada SSH |
| `VPS_APP_PATH` | `/var/www/arpgov` |

Após configurar, cada `push` na `main` executa o deploy automaticamente.

## O que NÃO vai no Git

- `.env` — senhas (só no VPS)
- `instance/portal.db` — banco SQLite
- `static/uploads/` — imagens e anexos
