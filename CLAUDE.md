# InvigilAI
"Veyon shows 60 screens. InvigilAI tells you which 3 to watch, and proves why."
Exam integrity system for institution-managed lab PCs.

## Stack
- /agent: Python (mss, Pillow, imagehash, psutil, pywin32, uiautomation, pynput, pyperclip, python-socketio client + websocket-client)
- /server: FastAPI + python-socketio (AsyncServer, ASGI), SQLite, uvicorn
- /dashboard: React + Vite + Tailwind + socket.io-client
- Windows first, Linux fallback (xdotool). LAN only.

## Event format (fixed)
{ "device_id": str, "type": "screen|window|alert|signal|control|heartbeat", "data": {}, "ts": int }

## Rules (strict)
- Never invent library functions, flags or APIs. Check installed package source if unsure, or say "unsure".
- Install packages first, then code. Keep requirements.txt / package.json real (pip freeze).
- After every change: run it and show real output. No "this should work".
- One step at a time. Stop and report after each step.
- Don't touch files outside the current task.
- On failure: show the actual error first, then fix.
- No mock/fake data unless I ask.
- Agent never logs actual keystrokes, only counts.
- Agent sends nothing when no exam session is active.

## UI style
Clean, minimal, dark, rounded cards, lots of whitespace, one accent color, red only for alerts.