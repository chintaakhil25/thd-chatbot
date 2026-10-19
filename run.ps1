# Start the THD Knowledge Assistant (backend + frontend) at http://localhost:8080
Set-Location $PSScriptRoot
$env:PYTHONWARNINGS = "ignore"
Start-Process "http://localhost:8080"
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8080
