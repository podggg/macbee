#!/usr/bin/env python3
"""UI lifecycle test only. No audio, devices, sockets, SSH, or capture."""
import json,signal,sys,time

def stop(sig,frame):
    print(json.dumps({'event':'mock_stopped','audio_opened':False}),flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
print(json.dumps({'event':'mock_started','audio_opened':False}),flush=True)
for i in range(5):time.sleep(1)
print(json.dumps({'event':'mock_completed','audio_opened':False}),flush=True)
