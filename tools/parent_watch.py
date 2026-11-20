"""Inherited-pipe liveness without Python buffered-stdin shutdown locks."""
import os
import select
import threading

class ParentWatch:
    def __init__(self, fd, on_disconnect):
        self.fd=fd
        self.on_disconnect=on_disconnect
        self.done=threading.Event()
        self.thread=threading.Thread(target=self._run,name='macbee-parent-watch')
    def start(self):
        self.thread.start()
        return self
    def _run(self):
        while not self.done.is_set():
            try:
                ready=select.select([self.fd],[],[],.1)[0]
                if not ready:continue
                os.read(self.fd,1) # unbuffered; no interpreter-finalization IO lock
            except (OSError,ValueError):
                pass
            if not self.done.is_set():self.on_disconnect()
            return
    def close(self):
        self.done.set()
        self.thread.join(timeout=1)
        if self.thread.is_alive():raise RuntimeError('Parent watcher did not terminate')
