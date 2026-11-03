"""Physical memory of a process on macOS (libproc's proc_pid_rusage). `ps -o rss` misses what Metal (MPS) holds for
torch: on 2026-10-21 a daemon with a 15 GB footprint showed 13 MB of RSS. The footprint is what Activity Monitor
calls Memory and what pushes the Mac into swap."""
from __future__ import annotations

import ctypes
import os
import struct
import sys

_RUSAGE_INFO_V4 = 4
_lib = None


def usage(pid: int | None = None) -> dict | None:
    """{"footprint_mb", "peak_footprint_mb", "resident_mb"} of pid (default: this process); None when unknown."""
    global _lib
    if sys.platform != "darwin":
        return None
    try:
        if _lib is None:
            _lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buf = ctypes.create_string_buffer(1024)
        if _lib.proc_pid_rusage(int(pid or os.getpid()), _RUSAGE_INFO_V4, buf) != 0:
            return None
    except (OSError, AttributeError):
        return None
    # rusage_info_v4: 16-byte uuid, then uint64 fields; resident_size is the 7th, phys_footprint the 8th and
    # lifetime_max_phys_footprint the 29th.
    v = struct.unpack_from("<29Q", buf, 16)
    return {"footprint_mb": v[7] >> 20, "peak_footprint_mb": v[28] >> 20, "resident_mb": v[6] >> 20}
