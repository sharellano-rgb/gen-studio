param([int]$Port = 8790)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
if ($Port -ne 8790) { throw 'Esta etapa utiliza el puerto 8790. Revisa la configuración antes de cambiarlo.' }
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Falta .venv. Sigue la instalación aislada del README.' }
if (!(Test-Path -LiteralPath (Join-Path $taskRoot 'frontend\dist\index.html'))) { throw 'Falta el build de React. Ejecuta npm run build en frontend.' }
Set-Location -LiteralPath $taskRoot
Write-Host "GEN STUDIO: http://127.0.0.1:$Port"
Write-Host 'Modo auditoría. Ctrl+C para detener solo este servidor.'
& $pythonPath -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
