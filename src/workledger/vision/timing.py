class FrameCadence:
    """Keep a publish deadline so e.g. a 12 FPS source can deliver 10 FPS.

    Starting a fresh interval after each frame unnecessarily discards every
    second frame when the source period is just shorter than the target period.
    """

    def __init__(self):
        self.next_at = 0.0

    def ready(self, at, fps):
        if at + 1e-9 < self.next_at:
            return False
        self.next_at += 1 / fps
        if self.next_at <= at:
            self.next_at = at + 1 / fps
        return True
