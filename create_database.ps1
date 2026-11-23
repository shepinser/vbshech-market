param([string]$DatabasePath = (Join-Path $PSScriptRoot 'database.accdb'))
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $DatabasePath) { exit 0 }
$engine = New-Object -ComObject DAO.DBEngine.120
$database = $engine.CreateDatabase($DatabasePath, ';LANGID=0x0409;CP=1252;COUNTRY=0', 128)
$database.Close()
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($database)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($engine)
