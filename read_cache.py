"""Short-lived read data shared only while an exchange cycle is active."""
import time
from contextlib import contextmanager

class ReadCache:
    def __init__(self):
        self.depth = 0
        self.values = {}
    @contextmanager
    def cycle(self):
        if not self.depth: self.values.clear()
        self.depth += 1
        try: yield
        finally:
            self.depth -= 1
            if not self.depth: self.values.clear()
    def read(self,key,loader):
        if not self.depth: return loader()
        value = self.values.get(key)
        if value is None or time.monotonic()-value[0]>=5:
            result=loader()
            self.values[key]=(time.monotonic(),result)
            return result
        return value[1]
    def invalidate(self): self.values.clear()
