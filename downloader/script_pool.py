import time


class ScriptScorePool:
    def __init__(self, script_ids):
        self.script_ids = list(script_ids or [])
        self.stats = {
            sid: {"ok": 0, "fail": 0, "latency": 0.0, "dead_until": 0.0}
            for sid in self.script_ids
        }

    def report_ok(self, sid, latency):
        s = self.stats.setdefault(sid, {"ok": 0, "fail": 0, "latency": 0.0, "dead_until": 0.0})
        s["ok"] += 1
        s["latency"] = latency if s["latency"] <= 0 else (s["latency"] * 0.8 + latency * 0.2)

    def report_fail(self, sid, cooldown=60):
        s = self.stats.setdefault(sid, {"ok": 0, "fail": 0, "latency": 0.0, "dead_until": 0.0})
        s["fail"] += 1
        s["dead_until"] = time.time() + cooldown

    def alive(self):
        now = time.time()
        return [sid for sid in self.script_ids if self.stats.get(sid, {}).get("dead_until", 0) <= now]
