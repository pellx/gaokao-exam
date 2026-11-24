"""Run isolated Win32 platform tests: python -I _orchestration/native/test_platform.py.

Requires MinGW GCC (GCC environment variable overrides C:/mingw64/bin/gcc.exe).
Only temporary files and the fixture's own child processes are created.
"""
import ctypes
import ctypes.wintypes as win
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time


class Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", win.DWORD),
        ("offset_high", win.DWORD),
        ("event", win.HANDLE),
    ]


class FileLock(ctypes.Structure):
    _fields_ = [("handle", win.HANDLE), ("overlap", Overlapped)]


def check_python_and_files(dll_path, directory):
    import msvcrt
    from _ctypes import FreeLibrary

    lib = ctypes.CDLL(str(dll_path), use_last_error=True)
    lib.lock_file.argtypes = [ctypes.POINTER(FileLock), ctypes.c_char_p, ctypes.c_uint]
    lib.unlock_file.argtypes = [ctypes.POINTER(FileLock)]
    lib.make_dirs.argtypes = [ctypes.c_char_p]
    lib.write_atomic.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.write_file.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.absolute_path.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.absolute_path.restype = ctypes.c_void_p
    lib.snapshot_tree.argtypes = [ctypes.c_char_p]
    lib.snapshot_tree.restype = ctypes.c_void_p
    # This project's GCC uses the Windows Universal CRT.
    free = ctypes.CDLL("ucrtbase").free
    free.argtypes = [ctypes.c_void_p]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        win.LPCWSTR, win.DWORD, win.DWORD, ctypes.c_void_p,
        win.DWORD, win.DWORD, win.HANDLE,
    ]
    kernel.CreateFileW.restype = win.HANDLE
    kernel.CloseHandle.argtypes = [win.HANDLE]
    try:
        root = directory / "extra"
        root.mkdir()
        path = root / "互斥.lock"
        path.write_bytes(b"original")
        encoded = str(path).encode("utf-8")
        with path.open("r+b") as handle:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            lock = FileLock()
            start = time.monotonic()
            assert not lib.lock_file(ctypes.byref(lock), encoded, 0)
            assert time.monotonic() - start < 0.2
            start = time.monotonic()
            assert not lib.lock_file(ctypes.byref(lock), encoded, 90)
            assert 0.075 <= time.monotonic() - start < 1
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            assert lib.lock_file(ctypes.byref(lock), encoded, 0)
            try:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    pass
                else:
                    raise AssertionError("Python acquired the byte locked by C")
            finally:
                lib.unlock_file(ctypes.byref(lock))
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        assert path.read_bytes() == b"original"

        target = root / "atomic.json"
        target.write_bytes(b"old")
        encoded_target = str(target).encode("utf-8")

        def block_replacement():
            handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
            assert handle != ctypes.c_void_p(-1).value
            return handle

        handle = block_replacement()
        timer = threading.Timer(0.18, lambda: kernel.CloseHandle(handle))
        timer.start()
        try:
            assert lib.write_atomic(encoded_target, b"new", 3)
        finally:
            timer.join()
        assert target.read_bytes() == b"new"
        handle = block_replacement()
        try:
            assert not lib.write_atomic(encoded_target, b"bad", 3)
        finally:
            kernel.CloseHandle(handle)
        assert target.read_bytes() == b"new"
        assert not list(root.glob("*.tmp-*"))
        long_basename = root / ("a" * 240 + ".json")
        assert lib.write_atomic(str(long_basename).encode("utf-8"), b"long-name", 9)
        assert long_basename.read_bytes() == b"long-name"

        deep = root
        for index in range(25):
            deep /= f"long_component_{index:02d}"
        assert len(str(deep)) > 260
        assert lib.make_dirs(str(deep).encode("utf-8"))
        assert lib.write_file(str(deep / "中文.bin").encode("utf-8"), b"long-path", 9)
        result = lib.snapshot_tree(str(root).encode("utf-8"))
        assert result
        try:
            assert "中文.bin" in ctypes.string_at(result).decode("utf-8")
        finally:
            free(result)
        result = lib.absolute_path(
            str(root).encode("utf-8"), str(deep / "中文.bin").encode("utf-8")
        )
        assert result
        free(result)
        assert not lib.snapshot_tree(str(root / "missing").encode("utf-8"))
        assert not lib.make_dirs(b"\xff")
    finally:
        FreeLibrary(lib._handle)
        lib._handle = 0
    print("PASS Python msvcrt interoperability, atomic retries, UTF-8 long paths")


def check_controller_exit(executable, directory, env):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [win.DWORD, win.BOOL, win.DWORD]
    kernel.OpenProcess.restype = win.HANDLE
    kernel.WaitForSingleObject.argtypes = [win.HANDLE, win.DWORD]
    kernel.CloseHandle.argtypes = [win.HANDLE]
    root = directory / "job-close"
    root.mkdir()
    marker = root / "NEVER"
    log = root / "child.log"
    handles = []
    process = subprocess.Popen(
        [str(executable), "supervise", str(marker), str(log)],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = process.stdout.readline()
        assert line.startswith("root="), (line, process.stderr.read())
        root_pid = int(line.split("=")[1])
        deadline = time.monotonic() + 3
        while True:
            text = log.read_text() if log.exists() else ""
            if text.startswith("leaf="):
                break
            assert time.monotonic() < deadline, "grandchild did not start"
            time.sleep(0.01)
        child_pid = int(text.strip().split("=")[1])
        for pid in (root_pid, child_pid):
            handle = kernel.OpenProcess(0x100000, False, pid)
            assert handle
            handles.append(handle)
        # Simulate the controller exiting without calling run_child's cleanup.
        process.terminate()
        process.wait(timeout=3)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 3000) == 0
        assert not marker.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
        for handle in handles:
            kernel.CloseHandle(handle)
    print("PASS controller exit closes Job and kills child and grandchild")


def main():
    if os.name != "nt":
        raise SystemExit("These tests require Windows and MinGW GCC")
    source = Path(__file__).resolve().parent
    gcc_name = os.environ.get("GCC", "C:/mingw64/bin/gcc.exe")
    gcc = Path(gcc_name) if Path(gcc_name).is_file() else Path(shutil.which(gcc_name) or "")
    if not gcc.is_file():
        raise SystemExit("MinGW GCC not found; set GCC to its executable path")
    env = dict(os.environ)
    env["PATH"] = str(gcc.parent) + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="gaokao-platform-tests-") as temporary:
        directory = Path(temporary)
        obj = directory / "platform.o"
        executable = directory / "test-platform.exe"
        dll = directory / "platform-test.dll"
        common = [str(gcc), "-std=c11", "-Wall", "-Wextra", "-Werror"]
        commands = [
            [*common, "-c", str(source / "platform.c"), "-o", str(obj)],
            [*common, "-municode", str(source / "test_platform_fixture.c"),
             str(obj), "-lbcrypt", "-o", str(executable)],
            [*common, "-shared", str(obj), "-lbcrypt", "-o", str(dll)],
        ]
        for command in commands:
            subprocess.run(command, cwd=directory, env=env, check=True, timeout=60)
        print("PASS GCC -std=c11 -Wall -Wextra -Werror", flush=True)
        fixture_directory = directory / "fixture"
        fixture_directory.mkdir()
        subprocess.run([str(executable)], cwd=fixture_directory, env=env, check=True, timeout=30)
        check_python_and_files(dll, directory)
        check_controller_exit(executable, directory, env)
    print("All isolated platform tests passed; temporary files removed")


if __name__ == "__main__":
    main()
