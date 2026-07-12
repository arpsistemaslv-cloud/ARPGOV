# Envia banco SQLite e uploads do PC para o VPS (primeira migracao).
# Uso no PowerShell:
#   .\scripts\upload-data-to-vps.ps1 -VpsHost SEU_IP -VpsUser deploy

param(
    [Parameter(Mandatory = $true)]
    [string]$VpsHost,

    [string]$VpsUser = "deploy",
    [string]$RemotePath = "/var/www/arpgov",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

$db = Join-Path $ProjectRoot "instance\portal.db"
$uploads = Join-Path $ProjectRoot "static\uploads"

Write-Host ">>> Destino: ${VpsUser}@${VpsHost}:${RemotePath}" -ForegroundColor Cyan

if (Test-Path $db) {
    Write-Host ">>> Enviando portal.db..."
    ssh "${VpsUser}@${VpsHost}" "mkdir -p ${RemotePath}/instance"
    scp $db "${VpsUser}@${VpsHost}:${RemotePath}/instance/portal.db"
} else {
    Write-Warning "Banco nao encontrado: $db"
}

if (Test-Path $uploads) {
    $count = (Get-ChildItem -Path $uploads -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($count -gt 0) {
        Write-Host ">>> Enviando static/uploads ($count arquivos)..."
        scp -r $uploads "${VpsUser}@${VpsHost}:${RemotePath}/static/"
    } else {
        Write-Host ">>> uploads/ vazio - nada a enviar."
    }
} else {
    Write-Warning "Pasta uploads nao encontrada: $uploads"
}

Write-Host ">>> Concluido. No VPS: sudo systemctl restart arpgov" -ForegroundColor Green
