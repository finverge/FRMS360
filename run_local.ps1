# Run the control plane locally against a native PostgreSQL (no Docker).
# Prereqs: a Python venv at .venv with deps installed:
#   python -m venv .venv
#   .\.venv\Scripts\python -m pip install -r requirements.txt
#   .\.venv\Scripts\python -m pip install -e packages/cp_common
# And a 'controlplane' database owned by role 'cp' (see README section 2b).

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Create the venv first (see header of this script)." }

# Idempotent restart: stop anything already listening on our ports before starting fresh
# copies. Matched by port, not by process name - a stray unrelated python.exe must never
# be killed because it happened to also be named python.exe.
$knownPorts = 8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088
$stopped = @()
foreach ($port in $knownPorts) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($conn in $conns) {
    if ($stopped -contains $conn.OwningProcess) { continue }
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    $stopped += $conn.OwningProcess
    Write-Host "stopped process $($conn.OwningProcess) on port $port"
  }
}
if ($stopped.Count -gt 0) {
  Write-Host "waiting for $($stopped.Count) stopped process(es) to release their ports..."
  Start-Sleep -Seconds 2
}

# Local (non-Docker) service topology + DB. These OS env vars override .env.
$env:PYTHONPATH        = $root
$env:DATABASE_URL      = "postgresql+psycopg2://cp:cp_password@localhost:5432/controlplane"
# 127.0.0.1, not localhost. On Windows "localhost" resolves to ::1 first and falls back
# to IPv4 only after roughly two seconds - measured, not assumed. The gateway's connect
# timeout is 2s, so every cold connection sat right on that boundary and would
# intermittently time out for no reason anyone could reproduce.
$env:TENANT_SERVICE_URL   = "http://127.0.0.1:8081"
$env:BRANDING_SERVICE_URL = "http://127.0.0.1:8082"
$env:CONFIG_SERVICE_URL   = "http://127.0.0.1:8083"
$env:ANALYTICS_SERVICE_URL = "http://127.0.0.1:8084"
$env:INGESTION_SERVICE_URL  = "http://127.0.0.1:8085"
$env:NOTIFICATION_SERVICE_URL = "http://127.0.0.1:8087"
# Lane A sits in the payment path, so it gets its own port and its own
# database role - see the HLD on why its availability class differs.
$env:DECISION_SERVICE_URL     = "http://127.0.0.1:8086"
$env:ANALYTICS_ENGINE      = "postgres"
# Tenant configuration is cached against a shared generation token rather than a
# per-process timer, so replicas cannot serve different generations of the same config.
# 'memory' is process-local and is refused outright when APP_REPLICAS > 1.
$env:CACHE_BACKEND         = "database"
$env:APP_REPLICAS          = "1"
$env:AUTO_CREATE_TABLES = "false"   # schema is owned by Alembic; run: alembic upgrade head
$env:SEED_DEMO_TENANT   = "false"   # seed richer data via scripts/api_seed.py instead
$env:SEED_ADMIN_EMAIL   = "admin@finverge.local"
$env:SEED_ADMIN_PASSWORD= "ChangeMe123!"

# Each service connects as its own database role and resolves unqualified table names
# against only the schemas it owns. Reaching for a neighbour's table fails in Postgres,
# not by convention. Create the roles once with:
#   python scripts\provision_db_roles.py --superuser-url postgresql+psycopg2://postgres:postgres@localhost:5432/controlplane
$dbHost = "localhost:5432/controlplane"
$svcs = @(
  @{ n = "tenant-service";   app = "services.tenant_service.app.main:app";   port = 8081
     role = "svc_tenant";    path = "tenant,platform" },
  @{ n = "branding-service"; app = "services.branding_service.app.main:app"; port = 8082
     role = "svc_branding";  path = "branding,platform" },
  @{ n = "config-service";   app = "services.config_service.app.main:app";   port = 8083
     role = "svc_config";    path = "config,platform" },
  @{ n = "ingestion-service"; app = "services.ingestion_service.app.main:app"; port = 8085
     role = "svc_ingestion"; path = "ingestion,platform" },
  @{ n = "notification-service"; app = "services.notification_service.app.main:app"; port = 8087
     role = "svc_notify"; path = "notify,platform" },
  @{ n = "decision-service"; app = "services.decision_service.app.main:app"; port = 8086
     role = "svc_decision"; path = "decision,platform" },
  @{ n = "analytics-service"; app = "services.analytics_service.app.main:app"; port = 8084
     role = "svc_analytics"; path = "analytics,cases,ingestion,platform" },
  @{ n = "lane-c-service";   app = "services.lane_c_service.app.main:app";   port = 8088
     role = "svc_lanec";     path = "lane_c,platform" },
  # The gateway proxies and serves static assets; it holds no database role of its own.
  @{ n = "gateway";          app = "services.gateway.app.main:app";          port = 8080
     role = ""; path = "" }
)

$procs = @()
foreach ($s in $svcs) {
  if ($s.role) {
    $env:DATABASE_URL   = "postgresql+psycopg2://$($s.role):cp_password@$dbHost"
    $env:DB_SEARCH_PATH = $s.path
    Write-Host "starting $($s.n) on :$($s.port)  [db role $($s.role) -> $($s.path)]"
  } else {
    Write-Host "starting $($s.n) on :$($s.port)"
  }
  $procs += Start-Process -FilePath $py -PassThru -NoNewWindow `
    -ArgumentList @("-m", "uvicorn", $s.app, "--host", "0.0.0.0", "--port", "$($s.port)", "--reload")
}
# Migrations run as the owning role, never as a service role: a process that can ALTER
# its tables while serving traffic can also drop them.
$env:DATABASE_URL = "postgresql+psycopg2://cp:cp_password@$dbHost"

Write-Host "`nControl plane running. Console: http://localhost:8080  (admin@finverge.local / ChangeMe123!)"
Write-Host "Seed test data: .\.venv\Scripts\python scripts\api_seed.py"
Write-Host "Press Ctrl+C to stop all services."
try { Wait-Process -Id ($procs.Id) } finally { $procs | ForEach-Object { $_.Kill() } }
