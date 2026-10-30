"""Manual test: run main.py first, then `python test_client.py`."""
import asyncio
import json
import time
import urllib.request

import socketio

URL = "http://127.0.0.1:8000"


def get_devices():
    with urllib.request.urlopen(f"{URL}/devices") as r:
        return json.loads(r.read())


async def main():
    received = asyncio.Event()
    dashboard = socketio.AsyncClient()
    agent = socketio.AsyncClient()

    @dashboard.on("event")
    async def on_event(data):
        print("dashboard received:", data)
        received.set()

    await dashboard.connect(URL, auth={"role": "dashboard"})
    await agent.connect(URL, auth={
        "role": "agent", "device_id": "lab-pc-01",
        "hostname": "LAB-PC-01", "os": "Windows 11",
    })
    print("GET /devices (agent connected):", get_devices())

    ev = {"device_id": "lab-pc-01", "type": "window",
          "data": {"title": "test"}, "ts": int(time.time())}
    print("agent sending:", ev)
    await agent.emit("event", ev)
    await asyncio.wait_for(received.wait(), timeout=5)

    await agent.disconnect()
    await asyncio.sleep(0.5)
    print("GET /devices (agent disconnected):", get_devices())
    await dashboard.disconnect()
    print("PASS")


asyncio.run(main())
