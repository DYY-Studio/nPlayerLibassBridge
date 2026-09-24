"""Report the host tools required to build and patch the bridge."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


TOOLS = ("clang", "xcrun", "git", "cmake", "ninja", "ldid")


def ios_sdk_path() -> Path | None:
    if shutil.which("xcrun") is None:
        return None
    result = subprocess.run(
        ["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    path = Path(value) if value else None
    if result.returncode != 0 or path is None or not path.is_dir():
        return None
    return path


def report() -> dict[str, object]:
    tools = {name: shutil.which(name) for name in TOOLS}
    sdk = ios_sdk_path()
    missing = [name for name, path in tools.items() if path is None]
    if sdk is None:
        missing.append("ios_sdk")
    return {"tools": tools, "ios_sdk": sdk, "missing": missing}


def main() -> int:
    status = report()
    for name, path in status["tools"].items():
        print(f"{name}={'missing' if path is None else path}")
    sdk = status["ios_sdk"]
    print(f"ios_sdk={'missing' if sdk is None else sdk}")
    if status["missing"]:
        print("required=missing: " + ",".join(status["missing"]))
        return 1
    print("required=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
