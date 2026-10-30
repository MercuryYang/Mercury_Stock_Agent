$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install uv==0.13.6
if ($LASTEXITCODE -ne 0) { throw 'uv bootstrap failed' }
& .\.venv\Scripts\uv.exe sync --locked
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
& .\.venv\Scripts\trading-agent.exe init-db
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
