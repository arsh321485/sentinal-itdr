#Requires -Version 5.1
<#
.SYNOPSIS
  Start SentinelForge ITDR locally on Windows (Docker Desktop required).

.EXAMPLE
  .\scripts\start-local-windows.ps1
  .\scripts\start-local-windows.ps1 -Stop
#>
param(
    [switch]$Stop,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SfDir = Join-Path $RepoRoot "sentinelforge"
$DataDir = Join-Path $SfDir "data"
$CertsDir = Join-Path $SfDir "certs"
$EnvFile = Join-Path $SfDir ".env"

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Env {
    if (-not (Test-Path $EnvFile)) {
        Copy-Item (Join-Path $SfDir ".env.local.example") $EnvFile
        Write-Host "[ok] Created .env from .env.local.example" -ForegroundColor Green
    }
}

function Ensure-Dirs {
    $subdirs = @(
        "redpanda", "postgres", "redis", "minio", "neo4j", "vault",
        "prometheus", "grafana", "loki", "geolite2"
    )
    foreach ($d in $subdirs) {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
    }
    New-Item -ItemType Directory -Force -Path $CertsDir | Out-Null
}

function Ensure-Certs {
    $cert = Join-Path $CertsDir "server.pem"
    if (Test-Path $cert) { return }

    if (-not (Test-Command openssl)) {
        Write-Host "[warn] openssl not found — install Git for Windows or use WSL for TLS certs." -ForegroundColor Yellow
        Write-Host "       Portal HTTP on port 80 will still work; HTTPS on 443 may fail until certs exist."
        return
    }

    & openssl req -x509 -nodes -days 3650 -newkey rsa:2048 `
        -keyout (Join-Path $CertsDir "server-key.pem") `
        -out $cert `
        -subj "/CN=localhost/O=SentinelForge-Local"
    Copy-Item $cert (Join-Path $CertsDir "ca.pem")
    Write-Host "[ok] Generated self-signed TLS certs in sentinelforge/certs/" -ForegroundColor Green
}

function Invoke-Compose {
    param([string[]]$Args)
    Push-Location $SfDir
    try {
        & docker compose @Args
        if ($LASTEXITCODE -ne 0) { throw "docker compose failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

# --- main ---
if (-not (Test-Command docker)) {
    Write-Host "ERROR: Docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}

$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker is not running. Start Docker Desktop and wait until it is ready." -ForegroundColor Red
    exit 1
}

if ($Stop) {
    Write-Host "Stopping SentinelForge..." -ForegroundColor Cyan
    Invoke-Compose -Args @("-f", "docker-compose.itdr.yml", "--env-file", ".env", "down")
    Invoke-Compose -Args @("-f", "docker-compose.core.yml", "--env-file", ".env", "down")
    Write-Host "Stopped." -ForegroundColor Green
    exit 0
}

Ensure-Env
Ensure-Dirs
Ensure-Certs

if ($SetupOnly) {
    Write-Host "Setup complete (.env, data/, certs/). Run without -SetupOnly to start containers." -ForegroundColor Green
    exit 0
}

Write-Host "`n=== Starting ForgeCore (Layer 1) ===" -ForegroundColor Cyan
Invoke-Compose -Args @("-f", "docker-compose.core.yml", "--env-file", ".env", "up", "-d")

Write-Host "Waiting 45s for core services..." -ForegroundColor Yellow
Start-Sleep -Seconds 45

Write-Host "`n=== Starting ForgeID (Layer 2) ===" -ForegroundColor Cyan
Invoke-Compose -Args @("-f", "docker-compose.itdr.yml", "--env-file", ".env", "up", "-d", "--build")

Write-Host "`n=== SentinelForge ITDR is starting ===" -ForegroundColor Green
Write-Host @"

  Portal (identity-svc):  http://localhost:8000
  Envoy gateway:          http://localhost:80  |  https://localhost:443
  Grafana:                http://localhost:3000  (admin / see .env)
  Neo4j browser:          http://localhost:7474  (neo4j / see .env)
  Keycloak:               http://localhost:8080
  Prometheus:             http://localhost:9090

  Health check:  curl http://localhost:8000/health

  M365/Google connectors are OFF by default in local config.
  Edit sentinelforge/configs/identity-svc/config.yaml and add Vault creds to enable.

  Stop:  .\scripts\start-local-windows.ps1 -Stop

"@
