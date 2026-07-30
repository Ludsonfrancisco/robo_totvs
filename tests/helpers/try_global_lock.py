import os
import sys
from pathlib import Path
import time

from flows.common.locks import LockUnavailable, file_lock


try:
    with file_lock(Path(sys.argv[1]), wait_seconds=0):
        if len(sys.argv) > 2 and sys.argv[2] == "crash":
            os._exit(0)
        if len(sys.argv) > 3 and sys.argv[2] == "hold":
            print("locked", flush=True)
            time.sleep(float(sys.argv[3]))
except LockUnavailable:
    raise SystemExit(75)
raise SystemExit(0)
