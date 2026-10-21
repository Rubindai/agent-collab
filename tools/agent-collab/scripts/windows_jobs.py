#!/usr/bin/env python3
"""Windows Job Object supervision for Agent Collab process groups.

Windows has no process group that a later process can signal. Each tracked
group is instead a named Job Object, ``Local\\AgentCollab-<pgid>``, whose
pgid is the root process ID. The root holds the job's only lasting handle and
the job kills every member when that handle closes, so a group never outlives
its root. The job does not permit breakaway, so descendants, including
detached ones, stay inside it. A later host process opens the job by name to
count live members or terminate them.

Load this module only on Windows.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_DUP_HANDLE = 0x0040
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004
DUPLICATE_SAME_ACCESS = 0x0002
ERROR_FILE_NOT_FOUND = 2
ERROR_INVALID_PARAMETER = 87
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _function(name: str, restype: Any, *argtypes: Any) -> Any:
    function = getattr(_kernel32, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


_CreateJobObjectW = _function("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
_OpenJobObjectW = _function("OpenJobObjectW", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_SetInformationJobObject = _function(
    "SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
)
_QueryInformationJobObject = _function(
    "QueryInformationJobObject",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
)
_AssignProcessToJobObject = _function("AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
_TerminateJobObject = _function("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_OpenProcess = _function("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_GetProcessTimes = _function(
    "GetProcessTimes", wintypes.BOOL, wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)
)
_GetExitCodeProcess = _function("GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.LPDWORD)
_WaitForSingleObject = _function("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_IsProcessInJob = _function(
    "IsProcessInJob", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
)
_CreateToolhelp32Snapshot = _function("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
_Thread32First = _function("Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
_Thread32Next = _function("Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
_OpenThread = _function("OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_ResumeThread = _function("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_DuplicateHandle = _function(
    "DuplicateHandle",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
)
_GetCurrentProcess = _function("GetCurrentProcess", wintypes.HANDLE)
_CloseHandle = _function("CloseHandle", wintypes.BOOL, wintypes.HANDLE)


def job_name(pgid: int) -> str:
    return f"Local\\AgentCollab-{pgid}"


def _last_error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return ctypes.WinError(code, f"{action} failed: {ctypes.FormatError(code)}")


def _open_process(pid: int, access: int) -> Any:
    handle = _OpenProcess(access, False, pid)
    if not handle:
        if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
            raise ProcessLookupError(f"no process with pid {pid}")
        raise _last_error(f"OpenProcess({pid})")
    return handle


def _open_for_status(pid: int) -> tuple[Any, bool]:
    """Open pid for liveness checks; the flag says whether it can be waited on.

    Limited query access is granted for most processes of other users, so a
    reused PID still reads as a different process instead of unknown.
    """

    try:
        return _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE), True
    except PermissionError:
        return _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION), False


def _running(process: Any, waitable: bool) -> bool:
    if waitable:
        state = _WaitForSingleObject(process, 0)
        if state == WAIT_FAILED:
            raise _last_error("WaitForSingleObject")
        return state == WAIT_TIMEOUT
    # Without SYNCHRONIZE, exit code 259 reads as running, which fails closed.
    code = wintypes.DWORD()
    if not _GetExitCodeProcess(process, ctypes.byref(code)):
        raise _last_error("GetExitCodeProcess")
    return code.value == STILL_ACTIVE


def process_start_time(pid: int) -> str:
    """Return a live process's creation time in 100 ns units since 1601."""

    process, waitable = _open_for_status(pid)
    try:
        # An open handle keeps an exited process's record readable; exited is
        # not a match.
        if not _running(process, waitable):
            raise ProcessLookupError(f"process {pid} has exited")
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _GetProcessTimes(process, *(ctypes.byref(item) for item in times)):
            raise _last_error(f"GetProcessTimes({pid})")
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        _CloseHandle(process)


def pid_alive(pid: int) -> bool:
    """Return whether pid is live; unreadable evidence counts as live."""

    try:
        process, waitable = _open_for_status(pid)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:
        return _running(process, waitable)
    except OSError:
        return True
    finally:
        _CloseHandle(process)


def resume(pid: int) -> None:
    """Resume a process created with CREATE_SUSPENDED."""

    snapshot = _CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise _last_error("CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = _Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = _OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not thread:
                    raise _last_error(f"OpenThread({entry.th32ThreadID})")
                try:
                    if _ResumeThread(thread) == 0xFFFFFFFF:
                        raise _last_error(f"ResumeThread({entry.th32ThreadID})")
                finally:
                    _CloseHandle(thread)
                resumed += 1
            more = _Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _CloseHandle(snapshot)
    if not resumed:
        raise ProcessLookupError(f"process {pid} has no thread to resume")


def adopt(pid: int) -> None:
    """Make pid the root of a new kill-on-close job that only pid holds open.

    Create pid suspended and resume it only after this returns: assignment is
    not retroactive, so anything pid started earlier would be outside the job.
    The caller must kill pid if this raises.
    """

    name = job_name(pid)
    ctypes.set_last_error(0)
    job = _CreateJobObjectW(None, name)
    if not job:
        raise _last_error(f"CreateJobObject({name})")
    process = None
    try:
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            raise FileExistsError(f"Agent Collab job {name} already exists")
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise _last_error("SetInformationJobObject")
        process = _open_process(pid, PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_DUP_HANDLE)
        if not _AssignProcessToJobObject(job, process):
            raise _last_error(f"AssignProcessToJobObject({pid})")
        # The root's copy is not inheritable, so the job ends with the root
        # rather than with whichever descendant exits last.
        owned = wintypes.HANDLE()
        if not _DuplicateHandle(
            _GetCurrentProcess(), job, process, ctypes.byref(owned), 0, False, DUPLICATE_SAME_ACCESS
        ):
            raise _last_error(f"DuplicateHandle(job to {pid})")
    finally:
        if process is not None:
            _CloseHandle(process)
        # Closing the creator's handle leaves the root's copy as the only one.
        # After a failed handoff, closing it kills the assigned root.
        _CloseHandle(job)


def _open_job(pgid: int, access: int) -> Any:
    job = _OpenJobObjectW(access, False, job_name(pgid))
    if not job:
        if ctypes.get_last_error() == ERROR_FILE_NOT_FOUND:
            return None
        raise _last_error(f"OpenJobObject({job_name(pgid)})")
    return job


def _active_processes(job: Any) -> int:
    info = _BasicAccountingInformation()
    if not _QueryInformationJobObject(
        job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        raise _last_error("QueryInformationJobObject")
    return int(info.ActiveProcesses)


def group_alive(pgid: int) -> bool:
    """Return whether the job rooted at pgid still has a live member.

    Callers check the root's exact identity first. Once the root has exited
    its kill-on-close job is gone, so a missing name proves nothing is left
    there; a live, matching root keeps the group live on its own.
    """

    try:
        job = _open_job(pgid, JOB_OBJECT_QUERY)
    except OSError:
        # Unreadable evidence never proves the group ended.
        return True
    if job is None:
        return False
    try:
        return _active_processes(job) > 0
    except OSError:
        return True
    finally:
        _CloseHandle(job)


def terminate_group(pgid: int, timeout_seconds: float) -> dict[str, Any]:
    """Terminate every member of the job rooted at pgid and prove quiescence.

    Windows has no group-wide graceful signal, so the job is terminated
    directly. Callers hold the root's verified identity or Popen handle, so a
    live root with no visible job means the name is in another session's
    namespace, not that the group ended.
    """

    try:
        job = _open_job(pgid, JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE)
    except OSError as exc:
        return {"outcome": "permission_denied", "quiescent": False, "pgid": pgid, "details": str(exc)}
    if job is None:
        if pid_alive(pgid):
            return {
                "outcome": "job_not_visible",
                "quiescent": False,
                "pgid": pgid,
                "details": "run cancel from the Windows session that started the run",
            }
        return {"outcome": "not_running", "quiescent": True, "pgid": pgid}
    try:
        inside = wintypes.BOOL()
        if not _IsProcessInJob(_GetCurrentProcess(), job, ctypes.byref(inside)):
            raise _last_error("IsProcessInJob")
        if inside.value:
            raise ValueError("refusing to terminate the caller's own process group")
        if _active_processes(job) == 0:
            return {"outcome": "not_running", "quiescent": True, "pgid": pgid}
        if not _TerminateJobObject(job, 1):
            raise _last_error("TerminateJobObject")
        deadline = time.monotonic() + timeout_seconds
        while _active_processes(job) and time.monotonic() < deadline:
            time.sleep(0.05)
        quiescent = _active_processes(job) == 0
        return {"outcome": "killed" if quiescent else "cleanup_failed", "quiescent": quiescent, "pgid": pgid}
    except OSError as exc:
        return {"outcome": "cleanup_failed", "quiescent": False, "pgid": pgid, "details": str(exc)}
    finally:
        _CloseHandle(job)
