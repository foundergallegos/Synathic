$log = "e2e_run_log.txt"
Remove-Item $log -ErrorAction SilentlyContinue
"Starting E2E run: $(Get-Date)" | Out-File $log

# Stop any process on port 8000
$p = (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1)
if ($p) { Stop-Process -Id $p -Force; "Stopped process $p on port 8000" | Out-File -Append $log } else { "No process on port 8000" | Out-File -Append $log }

# Choose python from venv if available
if (Test-Path ".\.venv\Scripts\python.exe") { $py = ".\.venv\Scripts\python.exe" } else { $py = "python" }
"Using python: $py" | Out-File -Append $log

# Ensure customers table exists
& $py scripts/create_customers.py *>> $log

# Start backend (uvicorn) in background
$proc = Start-Process -FilePath $py -ArgumentList "-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000" -PassThru
"Started uvicorn PID $($proc.Id)" | Out-File -Append $log
Start-Sleep -Seconds 3

# First agent run (expect fail)
"--- First agent run (expect fail) ---" | Out-File -Append $log
& $py test_agent.py *>> $log

# Check DB after first run
"--- DB after first run ---" | Out-File -Append $log
& $py scripts/check_db.py *>> $log

# Insert customer to make verification pass
"--- Insert customer ---" | Out-File -Append $log
& $py scripts/insert_customer.py *>> $log

# Second agent run (expect pass)
"--- Second agent run (expect pass) ---" | Out-File -Append $log
& $py test_agent.py *>> $log

# Check DB after second run
"--- DB after second run ---" | Out-File -Append $log
& $py scripts/check_db.py *>> $log

# Stop backend
Stop-Process -Id $proc.Id -Force
"Stopped uvicorn PID $($proc.Id)" | Out-File -Append $log

"Finished E2E run: $(Get-Date)" | Out-File -Append $log
