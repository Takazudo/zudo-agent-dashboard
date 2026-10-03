"""macOS process metadata via libproc. No argv, environment or terminal reads."""
import ctypes as C


class BsdInfo(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ["flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved"]] + [
        ("comm", C.c_char * 16), ("name", C.c_char * 32)] + [
        (name, C.c_uint32) for name in ["nfiles", "pgid", "jobc", "tdev", "tpgid"]] + [
        ("nice", C.c_int32), ("start_sec", C.c_uint64), ("start_usec", C.c_uint64)]


def library():
    lib = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_listallpids.argtypes = [C.c_void_p, C.c_int]
    lib.proc_listallpids.restype = C.c_int
    lib.proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
    lib.proc_pidinfo.restype = C.c_int
    return lib


def mac_process(pid, lib=None):
    if int(pid) <= 0:
        return None
    lib = lib or library()
    info = BsdInfo()
    count = lib.proc_pidinfo(int(pid), 3, 0, C.byref(info), C.sizeof(info))
    if count != C.sizeof(info) or info.pid != int(pid):
        return None  # Exited or permission-denied processes remain unknown.
    name = bytes(info.comm).split(b"\0", 1)[0].decode("utf-8", "replace")
    return dict(pid=int(info.pid), parent=int(info.ppid),
                start=f"{info.start_sec}.{info.start_usec:06d}",
                agent=name if name in {"claude", "codex"} else None)


def mac_processes(lib=None):
    lib = lib or library()
    count = lib.proc_listallpids(None, 0)
    if not 0 < count < 100_000:
        raise OSError("Process inventory unavailable")
    capacity = min(count + 1024, 100_000)
    pids = (C.c_int * capacity)()
    actual = lib.proc_listallpids(pids, C.sizeof(pids))
    if actual < 0 or actual >= capacity:
        raise OSError("Process inventory incomplete")
    return {pid: value for pid in pids[:actual] if pid > 0 and (value := mac_process(pid, lib))}
