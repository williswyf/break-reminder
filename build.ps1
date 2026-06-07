$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --noconfirm `
  --noconsole `
  --onefile `
  --name "Break Reminder" `
  --add-data "assets\E.png;assets" `
  rest_reminder.py

Write-Host "Built: dist\Break Reminder.exe"
