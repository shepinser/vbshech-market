$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment' }
}
& $python -c "import flask, pyodbc, PIL"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install dependencies' }
}
& $python init_db.py
if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize Access database' }
& $python app.py
