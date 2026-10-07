# Запуск команды с жёстким ограничением по времени.
#
#   .\tools\with_timeout.ps1 -Seconds 15 -File python -Args "-m","pytest","-q"
#
# Если процесс не уложился — он убивается, и команда возвращает код 124.

param(
    [int]$Seconds = 15,
    [Parameter(Mandatory = $true)][string]$File,
    [string[]]$CmdArgs = @()
)

$log = Join-Path $env:TEMP ("tanksim_cmd_{0}.log" -f (Get-Random))
$err = "$log.err"
$p = Start-Process -FilePath $File -ArgumentList $CmdArgs -NoNewWindow -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError $err
if ($p.WaitForExit($Seconds * 1000)) {
    Get-Content $log -ErrorAction SilentlyContinue
    $errText = Get-Content $err -ErrorAction SilentlyContinue
    if ($errText) { $errText }
    $code = $p.ExitCode
    Remove-Item $log, $err -ErrorAction SilentlyContinue
    exit $code
}
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Write-Output "--- ПРЕВЫШЕН ЛИМИТ $($Seconds) с, процесс остановлен ---"
Get-Content $log -ErrorAction SilentlyContinue
$errText = Get-Content $err -ErrorAction SilentlyContinue
if ($errText) { $errText }
Remove-Item $log, $err -ErrorAction SilentlyContinue
exit 124