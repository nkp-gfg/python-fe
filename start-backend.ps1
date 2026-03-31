# Start host processes for FalconEye backend

# Activate venv
& "$PSScriptRoot\venv\Scripts\Activate.ps1"

# Start Celery worker in background
$celery = Start-Process -NoNewWindow -PassThru -FilePath "$PSScriptRoot\venv\Scripts\celery.exe" `
    -ArgumentList "-A backend.celery_app worker --loglevel=info --pool=threads --concurrency=4" `
    -WorkingDirectory $PSScriptRoot

Write-Host "Celery worker started (PID: $($celery.Id))" -ForegroundColor Green

# Start uvicorn in foreground (Ctrl+C stops everything)
try {
    & "$PSScriptRoot\venv\Scripts\uvicorn.exe" backend.api.main:app --reload
}
finally {
    Write-Host "`nStopping Celery worker..." -ForegroundColor Yellow
    Stop-Process -Id $celery.Id -Force -ErrorAction SilentlyContinue
    Write-Host "All processes stopped." -ForegroundColor Green
}
