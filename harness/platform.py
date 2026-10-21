"""Target platform: which memory probe, power check and machine description to use.

    macos       Apple Silicon + Metal. Memory = phys_footprint (unified memory, includes Metal buffers).
    linux-cuda  Ubuntu + NVIDIA GPU (llama.cpp built with -DGGML_CUDA=ON). Memory = host RSS + the server's GPU
                memory from NVML, reported separately and as a total.

Chosen with --platform on every entry point (or AES_PLATFORM=...), default macos.
"""

import os
import sys

PLATFORMS = ("macos", "linux-cuda")
DEFAULT = "macos"
_ENV = "AES_PLATFORM"


def current() -> str:
    return os.environ.get(_ENV, DEFAULT)


def set_platform(name: str):
    if name not in PLATFORMS:
        raise SystemExit(f"unknown platform '{name}', expected one of {PLATFORMS}")
    expected = {"macos": "darwin", "linux-cuda": "linux"}[name]
    if not sys.platform.startswith(expected):
        other = "linux-cuda" if sys.platform.startswith("linux") else "macos"
        raise SystemExit(f"--platform {name} needs {expected}, this machine is {sys.platform}: use --platform {other}")
    # child processes and later imports read the same choice
    os.environ[_ENV] = name


def add_argument(parser):
    parser.add_argument("--platform", choices=PLATFORMS, default=current(),
                        help=f"macos (Apple Silicon, Metal) or linux-cuda (Ubuntu, NVIDIA). Default: {DEFAULT}")


def apply(args):
    set_platform(args.platform)
