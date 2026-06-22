"""Permits `python -m camera_bringup ...`.

Early arg parsing: --instance setups ENV CAMERA_BRINGUP_INSTANCE до того как
spec.py / checks / fixers будут imported (т.к. они читают env at import time).
Без этого --instance не имел бы эффекта.
"""
import argparse
import os
import sys


def _early_setup_instance_env(argv: list[str]) -> None:
    """Pre-parse --instance из argv, set ENV. Не consum'ит argv."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--instance", default=None)
    args, _ = parser.parse_known_args(argv)
    if args.instance:
        os.environ["CAMERA_BRINGUP_INSTANCE"] = args.instance


_early_setup_instance_env(sys.argv[1:])

# Now import cli — it will import spec which reads ENV.
# E402 intentional: --instance must set ENV before any other camera_bringup
# import to take effect (spec.py reads ENV at import-time).
from camera_bringup.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
