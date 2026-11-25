param([string]$DatabasePath = (Join-Path $PSScriptRoot 'database.accdb'), [switch]$AllowSystemEvents)
$ErrorActionPreference = 'Stop'
if ((Test-Path -LiteralPath $DatabasePath) -and -not $AllowSystemEvents) { exit 0 }
$engine = New-Object -ComObject DAO.DBEngine.120
if ($AllowSystemEvents) {
    $database = $engine.OpenDatabase($DatabasePath)
    # У автоматического изменения статуса нет сотрудника-автора.
    $database.TableDefs.Item('ИсторияЗаказов').Fields.Item('КодСотрудника').Required = $false
} else {
    $database = $engine.CreateDatabase($DatabasePath, ';LANGID=0x0409;CP=1252;COUNTRY=0', 128)
}
$database.Close()
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($database)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($engine)
