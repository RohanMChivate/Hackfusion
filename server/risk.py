"""Integrity Risk Score per device: 0-100, raised by alerts, decaying over time."""

# Points added per alert, by rule. Rules not listed still alert but add nothing.
WEIGHTS = {
    "blocked-domain": 40,       # blocked AI (or messaging) site
    "blocked-app": 25,
    "blocked-app-running": 25,
    "paste-burst": 25,
    "remote-or-vm-tool": 50,    # tamper
    "multiple-monitors": 50,    # tamper
    "agent-restart": 50,        # tamper
    "agent-lost": 50,
    "too-many-switches": 5,
}
DECAY_PER_MIN = 2
MAX_SCORE = 100


def level(score):
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


class RiskScores:
    def __init__(self):
        self._scores = {}  # device_id -> (score, time.time() it was set)

    def current(self, device_id, now):
        score, at = self._scores.get(device_id, (0.0, now))
        return max(0.0, score - DECAY_PER_MIN * (now - at) / 60)

    def add(self, device_id, rule, now):
        """Apply decay up to now, add the rule's weight, cap at MAX_SCORE. Returns (before, after)."""
        before = self.current(device_id, now)
        after = min(MAX_SCORE, before + WEIGHTS.get(rule, 0))
        self._scores[device_id] = (after, now)
        return before, after

    def snapshot(self, now):
        return [
            {"device_id": d, "score": round(s, 1), "level": level(s)}
            for d in self._scores
            for s in [self.current(d, now)]
        ]
