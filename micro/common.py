import argparse
import asyncio
import time

from harness import platform

PORT = 8090


def parse_args(desc: str):
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--quick", action="store_true", help="fewer points and repetitions, for a smoke test")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--allow-battery", action="store_true")
    platform.add_argument(p)
    args = p.parse_args()
    platform.apply(args)
    from harness.results import check_power
    check_power(args.allow_battery or args.quick)
    return args


async def settle(seconds: float = 1.0):
    # let the server finish post-request work (slot release, cache bookkeeping) before sampling memory
    await asyncio.sleep(seconds)


def now() -> float:
    return time.monotonic()
