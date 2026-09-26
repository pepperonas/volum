#!/usr/bin/env python3
"""Build the relocatable engine runtime that ships inside the application.

The reasoning is in ``docs/adr/0003-packaging-runtime.md``. In short: the
bundle carries a real CPython rather than a frozen binary, because the Model
Manager builds a virtual environment per provider and a frozen binary has no
interpreter to build one from.

    python3 scripts/build_runtime.py                 # build
    python3 scripts/build_runtime.py --check         # verify an existing build
    python3 scripts/build_runtime.py --no-strip      # keep tests and pip

The result is ``apps/desktop/src-tauri/runtime/`` — an interpreter tree with
the engine installed into it, plus the ``uv`` binary the Model Manager needs to
create provider environments. It is never committed: it is a few hundred
megabytes of third-party binaries built from pinned inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
RUNTIME = ROOT / "apps" / "desktop" / "src-tauri" / "runtime"
MANIFEST = RUNTIME / "runtime.json"

#: The interpreter the bundle carries. Inside the engine's own
#: ``requires-python`` (>=3.11,<3.13) and the newer of the two.
PYTHON_VERSION = "3.12"

#: Directories inside site-packages that are never imported at run time.
#: numpy and scipy ship tens of megabytes of tests; the bundle does not run
#: them. Everything removed here is checked afterwards by importing the engine.
#:
#: ⚠️ `tests` only — NOT `testing` or `test`. ``numpy.testing`` is public API,
#: not a test directory, and scipy imports it on the way to being imported at
#: all; removing it produced a runtime that installed cleanly and could not
#: load scipy. The verification step below is what caught it, which is the
#: reason that step exists.
STRIPPABLE_NAMES = ("__pycache__", "tests")

#: Tooling the bundle does not need: uv installs provider environments, so the
#: interpreter does not have to be able to install anything itself.
STRIPPABLE_PACKAGES = ("pip", "_distutils_hack", "distutils-precedence.pth")


class BuildError(RuntimeError):
    pass


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        args, capture_output=True, text=True, **kwargs  # type: ignore[arg-type]
    )
    if result.returncode != 0:
        raise BuildError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def uv_binary() -> Path:
    found = shutil.which("uv")
    if found is None:
        raise BuildError("uv is not on PATH; see https://docs.astral.sh/uv/")
    return Path(found).resolve()


def managed_interpreter(uv: Path) -> Path:
    """The directory of a uv-managed CPython of the pinned version.

    uv downloads these with a checksum and they are exactly the
    python-build-standalone artefacts the ADR names, so this is a shorter path
    to the same thing than fetching and verifying a release by hand.
    """
    run([str(uv), "python", "install", PYTHON_VERSION])
    base = Path(run([str(uv), "python", "dir"]).stdout.strip())
    candidates = sorted(base.glob(f"cpython-{PYTHON_VERSION}.*"))
    if not candidates:
        raise BuildError(f"uv installed no cpython-{PYTHON_VERSION}.* under {base}")
    # Highest patch version wins; the names sort naturally within a minor.
    return candidates[-1]


def interpreter_path(tree: Path) -> Path:
    """Where the interpreter is inside a standalone tree, per platform."""
    if os.name == "nt":
        return tree / "python.exe"
    return tree / "bin" / f"python{PYTHON_VERSION}"


def site_packages(tree: Path) -> Path:
    if os.name == "nt":
        return tree / "Lib" / "site-packages"
    return tree / "lib" / f"python{PYTHON_VERSION}" / "site-packages"


def copy_interpreter(source: Path) -> None:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    # symlinks=True keeps bin/python3 -> python3.12 a link rather than a second
    # copy of the binary, which is how the tree is meant to be laid out.
    shutil.copytree(source, RUNTIME, symlinks=True)

    # uv marks its own installations "externally managed" so that nothing
    # installs into them behind its back — sound for the copy it manages, and
    # not true of this one. This tree is a build artefact whose whole purpose
    # is to have the engine installed into it.
    for marker in RUNTIME.rglob("EXTERNALLY-MANAGED"):
        marker.unlink()


def install_engine(uv: Path) -> None:
    python = interpreter_path(RUNTIME)
    if not python.exists():
        raise BuildError(f"no interpreter at {python} after copying")
    run(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            str(python),
            # The HTTP surface is what the shell starts, so the engine extra is
            # not optional here.
            f"{ENGINE}[engine]",
        ]
    )


def bundle_uv(uv: Path) -> None:
    """Put uv where the engine can find it.

    The Model Manager creates a virtual environment per provider. In a bundled
    application it cannot go looking on PATH for the tool that does it.
    """
    target = RUNTIME / "bin" if os.name != "nt" else RUNTIME / "Scripts"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / uv.name
    shutil.copy2(uv, destination)
    destination.chmod(0o755)


def strip(tree: Path) -> int:
    """Remove what the bundle never runs. Returns bytes freed."""
    freed = 0
    packages = site_packages(tree)

    for name in STRIPPABLE_PACKAGES:
        for path in packages.glob(f"{name}*"):
            freed += directory_size(path)
            remove(path)

    for path in sorted(packages.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in STRIPPABLE_NAMES:
            freed += directory_size(path)
            remove(path)

    # The interpreter's own standard library carries its test suite too.
    for path in tree.rglob("__pycache__"):
        freed += directory_size(path)
        remove(path)
    # The interpreter's own extras. `test` here is CPython's test suite, which
    # is a different thing from a package's `testing` module.
    for name in ("test", "idlelib", "tkinter", "turtledemo", "ensurepip"):
        for path in tree.rglob(name):
            if path.is_dir() and "site-packages" not in path.parts:
                freed += directory_size(path)
                remove(path)
    return freed


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def verify(tree: Path) -> dict[str, str]:
    """Prove the built runtime actually works.

    Every step above removes something. A build that is not exercised
    afterwards is a build that has not been shown to survive its own stripping,
    so this imports the whole engine, asks the CLI for its version, and starts
    the engine far enough to be refused for the right reason.
    """
    python = interpreter_path(tree)
    if not python.exists():
        raise BuildError(f"no interpreter at {python}")

    imports = run(
        [
            str(python),
            "-c",
            "import json, volum_core, volum_cli, volum_engine, trimesh, numpy, scipy, "
            "manifold3d, mcubes, fastapi, uvicorn;"
            "from volum_core.hardware import run_doctor;"
            "from volum_engine.app import create_app;"
            "r = run_doctor(deep=False);"
            'print(json.dumps({"version": r.volum_version, "runtime": r.recommended_runtime}))',
        ]
    )
    report = json.loads(imports.stdout.strip().splitlines()[-1])

    # Without a token the engine must refuse to start. That it refuses for this
    # reason proves the entry point is reachable and its checks are intact.
    refusal = subprocess.run(  # noqa: S603
        [str(python), "-m", "volum_engine.main"],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "VOLUM_ENGINE_TOKEN"},
    )
    if refusal.returncode != 2 or "VOLUM_ENGINE_TOKEN" not in refusal.stderr:
        raise BuildError(
            "the bundled engine did not refuse to start without a token; "
            f"exit {refusal.returncode}, stderr: {refusal.stderr[:400]}"
        )

    uv_in_bundle = bundled_uv_path(tree)
    if not uv_in_bundle.exists():
        raise BuildError(f"uv is missing from the runtime at {uv_in_bundle}")
    uv_version = run([str(uv_in_bundle), "--version"]).stdout.strip()

    return {
        "volum_version": report["version"],
        "recommended_runtime": report["runtime"],
        "uv": uv_version,
    }


def bundled_uv_path(tree: Path) -> Path:
    name = "uv.exe" if os.name == "nt" else "uv"
    return (tree / ("Scripts" if os.name == "nt" else "bin")) / name


def platform_tag() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def build(*, do_strip: bool) -> None:
    uv = uv_binary()
    print(f"uv          {uv}")

    source = managed_interpreter(uv)
    print(f"interpreter {source.name}")

    copy_interpreter(source)
    print(f"copied      {RUNTIME.relative_to(ROOT)}  ({directory_size(RUNTIME) / 1e6:.0f} MB)")

    install_engine(uv)
    print(f"engine      installed ({directory_size(RUNTIME) / 1e6:.0f} MB)")

    bundle_uv(uv)

    if do_strip:
        freed = strip(RUNTIME)
        print(f"stripped    {freed / 1e6:.0f} MB")

    checked = verify(RUNTIME)
    size = directory_size(RUNTIME)
    MANIFEST.write_text(
        json.dumps(
            {
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "platform": platform_tag(),
                "python": source.name,
                "stripped": do_strip,
                "bytes": size,
                **checked,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"verified    VOLUM {checked['volum_version']}, {checked['uv']}")
    print(f"done        {size / 1e6:.0f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify an existing build only")
    parser.add_argument("--no-strip", action="store_true", help="keep tests, pip and setuptools")
    args = parser.parse_args()

    try:
        if args.check:
            if not RUNTIME.exists():
                raise BuildError(f"nothing built at {RUNTIME}")
            checked = verify(RUNTIME)
            print(f"ok: VOLUM {checked['volum_version']}, {checked['uv']}")
        else:
            build(do_strip=not args.no_strip)
    except BuildError as error:
        print(f"\nbuild_runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
