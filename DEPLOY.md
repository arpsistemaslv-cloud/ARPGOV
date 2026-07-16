# Deploy ARPGOV — Hostinger VPS KVM4 + GitHub

Repositório: **https://github.com/arpsistemaslv-cloud/ARPGOV**

## Arquitetura no VPS

```
Internet → Nginx :443 → Gunicorn 127.0.0.1:8001 → ARPGOV (Flask)
```

Outros projetos podem usar portas `8002`, `8003`, etc., cada um com domínio próprio.

---

## Pré-requisitos

1. VPS KVM4 ativo na Hostinger (Ubuntu)
2. Acesso SSH como **root** (painel Hostinger → VPS → SSH)
3. Domínio com registro **A** apontando para o **IP do VPS**
4. Código já no GitHub (branch `main`)

---

## Instalação automática no VPS (recomendado)

Conecte no VPS:

```bash
ssh root@SEU_IP_DO_VPS
```

Execute (substitua domínio e e-mail):

```bash
curl -fsSL https://raw.githubusercontent.com/arpsistemaslv-cloud/ARPGOV/main/deploy/hostinger/setup-vps.sh | bash -s -- \
  --domain arpgov.seudominio.com.br \
  --email admin@seudominio.com.br
```

O script instala: Python, Nginx, Certbot, clona o GitHub, cria `.env`, systemd e HTTPS.

Depois edite as senhas:

```bash
nano /var/www/arpgov/.env
# PAINEL_ADMIN_PASSWORD e CRM_ADMIN_PASSWORD
systemctl restart arpgov
```

---

## Instalação manual (passo a passo)

### 1. Pacotes e usuário

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git ufw
adduser deploy
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
```

### 2. Clonar o projeto

```bash
mkdir -p /var/www && chown deploy:deploy /var/www
su - deploy
cd /var/www
git clone https://github.com/arpsistemaslv-cloud/ARPGOV.git arpgov
cd arpgov
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
chmod +x deploy.sh
```

### 3. Ambiente de produção

```bash
cp deploy/hostinger/env.production.example .env
nano .env
```

Substitua `ARPGOV_DOMAIN` pelo domínio real e defina senhas fortes.

### 4. systemd + Nginx

Como **root**:

```bash
cp /var/www/arpgov/deploy/hostinger/arpgov.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable arpgov && systemctl start arpgov

sed 's/ARPGOV_DOMAIN/seu-dominio.com.br/g' /var/www/arpgov/deploy/hostinger/nginx-arpgov.conf \
  > /etc/nginx/sites-available/arpgov
ln -s /etc/nginx/sites-available/arpgov /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

certbot --nginx -d seu-dominio.com.br
```

### 5. Permissão para deploy.sh

```bash
cp /var/www/arpgov/deploy/hostinger/sudoers-deploy /etc/sudoers.d/arpgov-deploy
chmod 440 /etc/sudoers.d/arpgov-deploy
```

---

## Migrar dados do PC (primeira vez)

No **Windows** (PowerShell na pasta do projeto):

```powershell
.\scripts\upload-data-to-vps.ps1 -VpsHost SEU_IP_DO_VPS -VpsUser deploy
```

Ou manualmente:

```powershell
scp "instance\portal.db" deploy@SEU_IP:/var/www/arpgov/instance/
scp -r "static\uploads" deploy@SEU_IP:/var/www/arpgov/static/
```

No VPS:

```bash
sudo systemctl restart arpgov
```

---

## Atualizações (rotina) — processo que funciona

### 1. No PC (Windows) — enviar código ao GitHub

```powershell
cd "C:\Users\Victor Hugo\Desktop\PortalGovCRM"
git add .
git commit -m "descrição da alteração"
git push origin main
```

### 2. No VPS — publicar no site

**Não use `sudo` no PowerShell do Windows.** Os comandos abaixo são só **dentro do SSH no servidor Linux**.

Conectar (PowerShell):

```powershell
ssh deploy@85.31.60.61
```

Se `deploy` não entrar, use `ssh root@85.31.60.61` e depois `su - deploy`.

No servidor (como usuário **deploy**):

```bash
cd /var/www/arpgov
git fetch origin main
git reset --hard origin/main
git log -1 --oneline
bash deploy.sh
```

O `git log` deve mostrar o commit mais recente do GitHub. O `deploy.sh` puxa dependências e reinicia o serviço `arpgov`.

### 3. No navegador

Abra o site e pressione **Ctrl+F5** (recarregar sem cache), principalmente após mudanças em CSS/JS.

---

### Erros comuns

| Problema | Solução |
|----------|---------|
| `sudo` não funciona no Windows | Conecte via SSH e rode os comandos **no servidor** |
| `dubious ownership` ao usar **root** | Use `su - deploy` e rode o deploy como **deploy**, ou: `git config --global --add safe.directory /var/www/arpgov` |
| Site não mudou após pull | Rode `bash deploy.sh` e **Ctrl+F5** no navegador |
| `Connection reset` no SSH | Conecte de novo e repita a partir do `git fetch` |
| Aviso “conexão não é privada” no celular (`www`) | Certificado cobre só o domínio sem `www`. Como **root**: `certbot --nginx -d arpgov.com -d www.arpgov.com --expand` e confira no DNS o A de `www` apontando para o mesmo IP |

---

### Atalho (se já estiver logado como deploy)

```bash
cd /var/www/arpgov && git fetch origin main && git reset --hard origin/main && bash deploy.sh
```

---

## GitHub Actions (deploy automático)

Em **GitHub → Settings → Secrets → Actions**:

| Secret | Valor |
|--------|--------|
| `VPS_HOST` | IP do VPS |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | chave privada SSH |
| `VPS_APP_PATH` | `/var/www/arpgov` |

Cada `push` na `main` executa o deploy.

---

## URLs após deploy

| Área | URL |
|------|-----|
| Site | `https://seu-dominio/` |
| Painel admin | `https://seu-dominio/admin` |
| CRM | `https://seu-dominio/crm` |
| Comercial (vendedor) | `https://seu-dominio/comercial/entrar` |
| Parceiros | `https://seu-dominio/parceiro/entrar` |

---

## O que NÃO vai no GitHub

| Item | Onde fica |
|------|-----------|
| `.env` | Só no VPS |
| `instance/portal.db` | Só no VPS |
| `static/uploads/` | Só no VPS |

---

## Arquivos de configuração no repositório

```
deploy/hostinger/
  setup-vps.sh          # instalação automática
  arpgov.service        # systemd
  nginx-arpgov.conf     # Nginx
  env.production.example
  sudoers-deploy
```

---

## Vários projetos no mesmo KVM4

| Projeto | Pasta | Porta | Domínio |
|---------|-------|-------|---------|
| ARPGOV | `/var/www/arpgov` | 8001 | `arpgov...` |
| Outro | `/var/www/outro` | 8002 | `app2...` |

Copie `arpgov.service` e `nginx-arpgov.conf` ajustando porta e pasta.

---

## Comandos úteis no VPS

```bash
sudo systemctl status arpgov    # status do app
sudo journalctl -u arpgov -f    # logs em tempo real
sudo nginx -t                   # testar Nginx
cd /var/www/arpgov && bash deploy.sh   # atualizar do GitHub (como usuário deploy)
```

**Produção ARPGOV:** IP `85.31.60.61` · domínio `https://arpgov.com` · pasta `/var/www/arpgov`
