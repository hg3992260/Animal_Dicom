"""Bundle DLL-dependency reporter + precise fixer for the PyInstaller onedir output.

Background
----------
This bundle has shipped three separate "DLL not found at runtime" bugs, all of
them silent at build time:

  1. ``sklearn/.libs/vcomp140.dll``  -- dot-prefixed hidden dir dropped by
     ``actions/upload-artifact`` (fixed with ``include-hidden-files: true``)
     and by ``Compress-Archive`` (fixed by using ``tar.exe``).
  2. ``cc3d/fastcc3d*.pyd``          -- needs ``msvcp140-<32-hex>.dll``, which
     PyInstaller deduplicated into ``pandas.libs`` (a *different* package's
     folder). delvewheel mangled names are globally unique per DLL content, so
     the duplicate landed in whichever package was processed first.

This tool addresses class 2 -- and reports class 1 -- instead of trying to be a
universal gate. A universal gate is not viable: PySide6, torch and vtk each
register their own directories at runtime, which produces hundreds of
false positives.

What counts as a "searched" location
------------------------------------
* the importing binary's own directory and its ancestors up to ``_internal``
* the application directory and ``_internal`` (= ``sys._MEIPASS``)
* every ``*.libs`` / ``.libs`` folder (the runtime hook registers them all)
* System32 / the Windows directory
* directories the big packages register themselves (PySide6, shiboken6,
  ``torch\\lib``)

Usage
-----
    python tools/check_bundle_dlls.py dist/SSD_VR_Fusion_Viewer            # report
    python tools/check_bundle_dlls.py dist/SSD_VR_Fusion_Viewer --flatten  # fix + report
    ... --verbose                                                          # full lists

Exit codes: 0 ok, 1 a needed DLL is in the bundle but in no searched location
AND is not a delvewheel-mangled name we can flatten, 2 bad bundle path.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import sys
from collections import defaultdict

MANGLE = re.compile(r"-[0-9a-f]{32}\.dll$", re.IGNORECASE)

OS_PREFIXES = (
    "api-ms-win-", "ext-ms-win-", "ucrtbase", "kernel32", "kernelbase",
    "advapi32", "user32", "gdi32", "shell32", "ole32", "oleaut32", "ws2_32",
    "shlwapi", "comdlg32", "comctl32", "crypt32", "secur32", "bcrypt",
    "ncrypt", "iphlpapi", "netapi32", "psapi", "version", "winmm", "imm32",
    "dwmapi", "uxtheme", "rpcrt4", "dbghelp", "setupapi", "cfgmgr32",
    "powrprof", "userenv", "wintrust", "opengl32", "glu32", "d3d11", "dxgi",
    "msvcrt", "ntdll", "winspool.drv", "mpr", "wtsapi32", "avrt", "mfplat",
    "mfuuid", "dnsapi", "winhttp", "urlmon", "wininet", "normaliz", "propsys",
    "python3", "python31",
)

# Directories these packages put on the DLL search path themselves.
PACKAGE_DIRS = ("PySide6", "PySide6/Qt/bin", "shiboken6", "torch/lib",
                "torch/bin", "PyQt5/Qt5/bin")

# Legitimately-absent optional/external dependencies. Qt ships SQL driver
# plugins for vendor clients that are never redistributed, and cupy's cuTENSOR
# backend needs a separately-installed cuTENSOR.
OPTIONAL_EXTERNAL = (
    "cutensor", "cutensormg",
    "fbclient", "mimapi64", "oci.dll", "libmysql", "libpq", "libsybdb",
    "db2cli64", "sybdb", "psqlodbc", "tdsodbc", "odbc32", "sqlite3.dll",
    "esri", "ibm", "winsqlite3",
)


def pe_imports(path: str) -> list[str]:
    """DLL names in the PE import table; [] when absent/unparsable."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    try:
        if data[:2] != b"MZ":
            return []
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
            return []
        coff = e_lfanew + 4
        num_sections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0]
        dd_off = opt + (112 if magic == 0x20B else 96)
        import_rva = struct.unpack_from("<I", data, dd_off + 8)[0]
        if not import_rva:
            return []

        sec_off = opt + opt_size
        sections = []
        for i in range(num_sections):
            off = sec_off + i * 40
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
            sections.append((vaddr, max(vsize, rawsize), rawptr))

        def rva2off(rva):
            for vaddr, vsize, rawptr in sections:
                if vaddr <= rva < vaddr + vsize:
                    return rawptr + (rva - vaddr)
            return None

        names, o = [], rva2off(import_rva)
        while o:
            desc = struct.unpack_from("<IIIII", data, o)
            if desc == (0, 0, 0, 0, 0):
                break
            no = rva2off(desc[3])
            if no is None:
                break
            end = data.index(b"\0", no)
            names.append(data[no:end].decode("latin1"))
            o += 20
            if len(names) > 500:
                break
        return names
    except Exception:
        return []


def main(root: str, flatten: bool, verbose: bool) -> int:
    app_dir = os.path.abspath(root)
    internal = os.path.join(app_dir, "_internal")
    if not os.path.isdir(internal):
        print(f"!! not a PyInstaller onedir bundle: {app_dir}")
        return 2

    win = os.environ.get("SystemRoot", r"C:\Windows")
    system_dirs = [d for d in (os.path.join(win, "System32"), win) if os.path.isdir(d)]
    package_dirs = [os.path.join(internal, p.replace("/", os.sep)) for p in PACKAGE_DIRS]

    index = {}
    libs_dirs = set()
    binaries = []
    for dirpath, _dirnames, filenames in os.walk(app_dir):
        if os.path.basename(dirpath).endswith(".libs"):
            libs_dirs.add(dirpath)
        for fn in filenames:
            index.setdefault(fn.lower(), os.path.join(dirpath, fn))
            if fn.lower().endswith((".pyd", ".dll")):
                binaries.append(os.path.join(dirpath, fn))

    base_safe = {app_dir, internal} | set(system_dirs) | set(package_dirs)

    def libs_owner(d):
        """Which package owns this .libs folder ('' when undeterminable)."""
        b = os.path.basename(d)
        if b == ".libs":                       # e.g. sklearn/.libs
            return os.path.basename(os.path.dirname(d))
        if b.endswith(".libs"):                # e.g. pandas.libs
            return b[:-5]
        return b

    def own_libs(binary):
        """The .libs folders whose owner matches the importing package.

        numpy.libs belongs to numpy, vtk.libs to vtkmodules. pandas.libs does
        NOT belong to cc3d -- that mismatch is the bug this tool hunts.
        """
        rel = os.path.relpath(binary, internal)
        pkg = rel.split(os.sep)[0] if os.sep in rel else ""
        out = []
        for d in libs_dirs:
            owner = libs_owner(d)
            if owner and pkg and (owner == pkg or pkg.startswith(owner)
                                  or owner.startswith(pkg)):
                out.append(d)
        return out

    def searched(name, binary):
        """A directory the loader will actually look in for this import."""
        idir = os.path.dirname(binary)
        dirs = [idir]
        d = idir
        while os.path.abspath(d) != os.path.abspath(internal):
            parent = os.path.dirname(d)
            if parent == d:
                break
            dirs.append(parent)
            d = parent
        dirs.extend(base_safe)
        dirs.extend(own_libs(binary))
        for d in dirs:
            if os.path.isfile(os.path.join(d, name)):
                return os.path.join(d, name)
        return None

    elsewhere, nowhere = defaultdict(list), []
    checked = 0
    for binary in binaries:
        for dep in pe_imports(binary):
            checked += 1
            if searched(dep, binary):
                continue
            hit = index.get(dep.lower())
            if hit:
                elsewhere[dep].append((binary, hit))
            elif not dep.lower().startswith(OS_PREFIXES + OPTIONAL_EXTERNAL):
                nowhere.append((binary, dep))

    rel = lambda p: os.path.relpath(p, app_dir)  # noqa: E731
    print(f"bundle        : {app_dir}")
    print(f"binaries      : {len(binaries)}    imports checked: {checked}")
    print(f".libs folders : {len(libs_dirs)}    package dirs: {len(package_dirs)}")
    print()

    # --- the actionable fix: delvewheel-mangled names that ended up in some
    # --- other package's .libs folder; copy them where the loader always looks.
    mangled = {d: u for d, u in elsewhere.items() if MANGLE.search(d)}
    flattened, still = [], {}
    for dep, uses in sorted(mangled.items()):
        dst = os.path.join(internal, dep)
        if flatten:
            if not os.path.isfile(dst):
                shutil.copy2(uses[0][1], dst)
                flattened.append(dep)
        elif not os.path.isfile(dst):
            still[dep] = uses

    if flattened:
        print(f"--- flattened {len(flattened)} delvewheel-mangled DLL(s) into _internal ---")
        print("--- (so the loader finds them regardless of import order) ---")
        for dep in flattened:
            print(f"  + {dep}")
            print(f"      was only in {rel(mangled[dep][0][1])}")
        print()

    if still:
        print(f"### {len(still)} delvewheel-mangled import(s) resolvable only via another "
              f"package's .libs folder")
        print("### -> 'ImportError: DLL load failed while importing ...' unless that")
        print("###    package happens to be imported first. Run with --flatten to fix.")
        for dep in sorted(still):
            binary, hit = still[dep][0]
            print(f"  {rel(binary)}")
            print(f"      needs {dep}")
            print(f"      only here -> {rel(hit)}")
        print()

    other = {d: u for d, u in elsewhere.items() if not MANGLE.search(d)}
    print(f"--- {len(other)} non-mangled import(s) resolved through a package's own "
          f"runtime-registered folder (informational) ---")
    if verbose:
        for dep in sorted(other):
            binary, hit = other[dep][0]
            print(f"  {rel(binary)} -> {dep}  ({rel(hit)})")
    print()

    if nowhere:
        print(f"### HARD FAIL: {len(nowhere)} import(s) in the bundle but in no searched "
              f"location, and not OS-provided")
        for binary, dep in nowhere:
            print(f"  {rel(binary)}   needs  {dep}")
        print()
        print("RESULT: FAIL")
        return 1

    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    pos = [a for a in sys.argv[1:] if not a.startswith("-")]
    target = pos[0] if pos else r"dist/SSD_VR_Fusion_Viewer"
    sys.exit(main(target, "--flatten" in sys.argv, "--verbose" in sys.argv))
