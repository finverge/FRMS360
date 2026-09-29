# Alembic wrapper. Sets PYTHONPATH/DATABASE_URL so alembic finds the models and the DB.
#
#   .\migrate.ps1                       # upgrade to head (most common)
#   .\migrate.ps1 current               # which revision is this DB on?
#   .\migrate.ps1 history               # list revisions
#   .\migrate.ps1 check                 # do the models differ from the schema?
#   .\migrate.ps1 downgrade -1          # roll back one revision
#   .\migrate.ps1 revision --autogenerate -m "add x"   # create a new migration
#
# Override the target DB with -Database, e.g.  .\migrate.ps1 -Database cp_test upgrade head

param(
  # Positional so `.\migrate.ps1 current` works; -Database stays named-only.
  [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
  [string[]]$AlembicArgs,
  [string]$Database = "controlplane"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Create the venv first - see README section 2b." }

$env:PYTHONPATH = $root
if (-not $env:DATABASE_URL -or $Database -ne "controlplane") {
  $env:DATABASE_URL = "postgresql+psycopg2://cp:cp_password@localhost:5432/$Database"
}

if (-not $AlembicArgs -or $AlembicArgs.Count -eq 0) { $AlembicArgs = @("upgrade", "head") }

Write-Host "alembic $($AlembicArgs -join ' ')  [db=$Database]" -ForegroundColor Cyan
& $py -m alembic @AlembicArgs
