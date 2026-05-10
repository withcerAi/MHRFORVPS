import time


class SpeedMeter:
    def __init__(self):
        self.last_t = time.time()
        self.last_b = 0
        self.speed = 0.0
        self.samples = []

    def update(self, downloaded):
        now = time.time()
        dt = now - self.last_t
        if dt >= 0.5:
            delta = max(0, int(downloaded or 0) - self.last_b)
            inst = delta / dt
            self.samples.append(inst)
            self.samples = self.samples[-6:]
            self.speed = sum(self.samples) / max(1, len(self.samples))
            self.last_t = now
            self.last_b = int(downloaded or 0)
        return self.speed
