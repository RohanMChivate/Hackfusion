"""Evaluate agent events against a policy profile and decide which alerts to raise."""
from collections import deque

# Windows shell surfaces (taskbar, start menu, search, lock screen, file explorer).
# The foreground passes through these during normal app switching, so they never
# count as "app not allowed".
SHELL_PROCESSES = {
    "explorer.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "lockapp.exe",
}
REALERT_AFTER = 60  # seconds a still-present violation waits before alerting again
SWITCH_WINDOW = 60
# Remote-control and virtualisation tools; matched as case-insensitive substrings of process names.
TAMPER_PROCESS_PATTERNS = ("anydesk", "teamviewer", "vmware", "virtualbox", "rustdesk")
HEARTBEAT_TIMEOUT = 10


def _lower(names):
    return {n.lower() for n in names}


def domain_matches(domain, patterns):
    """True if domain equals a pattern or is a subdomain of it."""
    return any(domain == p or domain.endswith("." + p) for p in patterns)


class Evaluator:
    def __init__(self):
        # device_id -> {"alerted": {key: last_alert_ts}, "switches": deque[ts],
        #               "fg": (process, domain), "instance": agent instance id}
        self.devices = {}

    def _state(self, device_id):
        return self.devices.setdefault(
            device_id, {"alerted": {}, "switches": deque(), "fg": None, "instance": None},
        )

    def _dedup(self, state, kind, found, now, realert=True):
        """Alert each violation when it first appears and, if realert, again every
        REALERT_AFTER seconds while it persists. Violations that went away are forgotten."""
        alerted = state["alerted"]
        current = {(kind, v["rule"], v["detail"]): v for v in found}
        for key in [k for k in alerted if k[0] == kind and k not in current]:
            del alerted[key]
        out = []
        for key, v in current.items():
            if key not in alerted or (realert and now - alerted[key] >= REALERT_AFTER):
                alerted[key] = now
                out.append(v)
        return out

    def evaluate(self, device_id, event_type, data, profile, now):
        """Return a list of {rule, severity, detail} to alert on for a window/signal event."""
        state = self._state(device_id)
        kind = data.get("kind")
        if event_type == "window" and kind == "active":
            return self._dedup(state, kind, self._active(state, data, profile, now), now)
        if event_type == "window" and kind == "processes":
            # background processes: once per episode (until the process stops), no 60s repeats
            return self._dedup(state, kind, self._processes(data, profile), now, realert=False)
        if event_type == "signal" and kind == "monitors":
            count = data.get("count", 0)
            found = [{"rule": "multiple-monitors", "severity": "high",
                      "detail": f"{count} monitors connected"}] if count > 1 else []
            return self._dedup(state, kind, found, now)
        if event_type == "signal" and kind == "paste_burst":
            # a one-off event: always alert, no dedup
            return [{"rule": "paste-burst", "severity": "high",
                     "detail": f"{data.get('chars')} chars on clipboard with "
                               f"{data.get('keystrokes_last_5s')} keystrokes in 5s in {data.get('app')}"}]
        return []

    def on_connect(self, device_id, instance, now):
        """Agent connected. A different instance id than last time means the agent process restarted."""
        state = self._state(device_id)
        previous, state["instance"] = state["instance"], instance
        if previous is not None and instance != previous:
            return [{"rule": "agent-restart", "severity": "high",
                     "detail": "agent process restarted"}]
        return []

    def check_heartbeat(self, device_id, last_heartbeat, now):
        """Alert once per outage when heartbeats stop for more than HEARTBEAT_TIMEOUT seconds."""
        state = self._state(device_id)
        silent = now - last_heartbeat
        found = [{"rule": "agent-lost", "severity": "high",
                  "detail": f"no heartbeat for more than {HEARTBEAT_TIMEOUT}s"}] \
            if silent > HEARTBEAT_TIMEOUT else []
        return self._dedup(state, "heartbeat", found, now, realert=False)

    def _active(self, state, data, profile, now):
        process = data.get("process") or ""
        proc = process.lower()
        domain = (data.get("domain") or "").lower()
        found = []

        if proc in _lower(profile["blocked_apps"]):
            found.append({"rule": "blocked-app", "severity": "high",
                          "detail": f"{process} in foreground"})
        elif (proc and profile["allowed_apps"] and proc not in SHELL_PROCESSES
              and proc not in _lower(profile["allowed_apps"])):
            found.append({"rule": "app-not-allowed", "severity": "medium",
                          "detail": f"{process} in foreground"})

        if domain:
            if domain_matches(domain, profile["blocked_domains"]):
                found.append({"rule": "blocked-domain", "severity": "high",
                              "detail": f"{domain} open in {process}"})
            elif profile["allowed_domains"] and not domain_matches(domain, profile["allowed_domains"]):
                found.append({"rule": "domain-not-allowed", "severity": "medium",
                              "detail": f"{domain} open in {process}"})

        # foreground switches: count changes of (app, domain), ignoring shell surfaces
        if proc and proc not in SHELL_PROCESSES:
            fg = (proc, domain)
            if state["fg"] is not None and fg != state["fg"]:
                state["switches"].append(now)
            state["fg"] = fg
        switches = state["switches"]
        while switches and now - switches[0] > SWITCH_WINDOW:
            switches.popleft()
        limit = profile["max_switches_per_min"]
        if len(switches) > limit:
            found.append({"rule": "too-many-switches", "severity": "medium",
                          "detail": f"more than {limit} app/site switches in the last minute"})
        return found

    def _processes(self, data, profile):
        names = data.get("names", [])
        blocked = _lower(profile["blocked_apps"])
        found = [{"rule": "blocked-app-running", "severity": "medium",
                  "detail": f"{name} is running"}
                 for name in sorted(n for n in names if n.lower() in blocked)]
        found += [{"rule": "remote-or-vm-tool", "severity": "high",
                   "detail": f"{name} is running"}
                  for name in sorted(n for n in names
                                     if any(p in n.lower() for p in TAMPER_PROCESS_PATTERNS))]
        return found
