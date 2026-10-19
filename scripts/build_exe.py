"""Script to build standalone HandControl Windows executable using PyInstaller.

Usage:
    python scripts/build_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def build() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    spec_file = root_dir / "handcontrol.spec"

    if not spec_file.exists():
        print(f"Error: Spec file not found at {spec_file}")
        return 1

    print("==================================================")
    print("HandControl Standalone Executable Builder (Phase 8)")
    print("==================================================")
    print(f"Working Directory: {root_dir}")
    print(f"Spec File:         {spec_file}")
    print("Packaging into dist/HandControl/ ...")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root_dir)

    result = subprocess.run(cmd, cwd=root_dir, env=env)

    if result.returncode != 0:
        print(f"\n[FAIL] PyInstaller build failed with return code {result.returncode}")
        return result.returncode

    exe_path = root_dir / "dist" / "HandControl" / "HandControl.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print("\n==================================================")
        print("[SUCCESS] HandControl.exe generated successfully!")
        print(f"Executable: {exe_path}")
        print(f"Size:       {size_mb:.2f} MB")
        print("You can run it directly with two clicks from Windows Explorer!")
        print("==================================================")
        return 0
    else:
        print(f"\n[FAIL] Expected executable not found at {exe_path}")
        return 1


if __name__ == "__main__":
    sys.exit(build())
