# Smart-Home Simulator Guide

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/smart-home/rooms
Invoke-RestMethod http://127.0.0.1:8000/api/v1/smart-home/devices?room=bedroom
$body = @{ action='brightness'; value=42; source='ui' } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType application/json -Body $body `
  http://127.0.0.1:8000/api/v1/smart-home/devices/light.bedroom_main/command
```

Development controls below `/api/v1/smart-home/simulator` set availability,
inject clearly labeled climate values, or force washer/vacuum completion. Inspect
authoritative state through `/history`, `/activities`, `/energy` and `/routines`.
PostgreSQL retains device state across backend restarts.
