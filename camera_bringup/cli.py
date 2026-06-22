"""CLI entry point для camera_bringup.

Команды:
  verify  — read-only audit всех checks
  apply   — выполнить fixers для checks которые в WARN/FAIL
  list    — перечислить доступные checks
"""
from __future__ import annotations

import argparse
import sys

from camera_bringup.check import exit_code, print_human, print_json, safe_run
from camera_bringup.checks import ALL_CHECKS, get_check
from camera_bringup.fixer import fixer_exit_code, print_fixer_results, run_fixer
from camera_bringup.fixers import ALL_FIXERS


def _filter_checks(only: list[str] | None):
    if not only:
        return ALL_CHECKS
    selected = set(only)
    return [(name, fn) for name, fn in ALL_CHECKS if name in selected]


def cmd_verify(args: argparse.Namespace) -> int:
    checks = _filter_checks(args.only)
    if args.only and len(checks) != len(args.only):
        missing = set(args.only) - {n for n, _ in checks}
        print(f"unknown check ids: {sorted(missing)}", file=sys.stderr)
        print(f"available: {[n for n, _ in ALL_CHECKS]}", file=sys.stderr)
        return 2

    ctx: dict = {}
    results = [safe_run(name, fn, ctx) for name, fn in checks]

    if args.json:
        print_json(results)
    else:
        print_human(results)

    return exit_code(results)


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply fixers для checks которые в WARN/FAIL.

    Перед каждым fixer'ом запускается соответствующий check:
      - если check OK → fixer SKIPPED
      - иначе → run plan → execute → re-verify
    """
    # 1. Сначала собираем ctx через все checks (чтобы fixers могли использовать
    #    sysfs_path и т.п. из usb_enumerate).
    ctx: dict = {}
    for name, fn in ALL_CHECKS:
        safe_run(name, fn, ctx)   # discard result, just populate ctx

    # 2. Определяем какие fixers запускать
    if args.only:
        selected = [(n, cls) for n, cls in ALL_FIXERS.items() if n in args.only]
        unknown = set(args.only) - set(ALL_FIXERS.keys())
        if unknown:
            print(f"unknown fixer ids: {sorted(unknown)}", file=sys.stderr)
            print(f"available: {list(ALL_FIXERS.keys())}", file=sys.stderr)
            return 2
    else:
        selected = list(ALL_FIXERS.items())

    if not selected:
        print("no fixers selected", file=sys.stderr)
        return 0

    # 3. Если требуется root — предупредить (или fail если --no-root)
    needs_root = any(cls.requires_root for _, cls in selected)
    if needs_root and not args.yes_root:
        print(
            "WARNING: некоторые fixers требуют root. Запустите через sudo, "
            "или передайте --yes-root если уверены что текущий пользователь имеет права.",
            file=sys.stderr,
        )

    # 4. Run fixers
    results = []
    for name, cls in selected:
        check_fn = get_check(name)
        fixer = cls()
        result = run_fixer(fixer, check_fn, ctx, dry_run=args.dry_run)
        results.append(result)
        if result.status.value == "FAILED" and not args.continue_on_fail:
            print(f"\nfixer {name} FAILED — останов. Используйте --continue-on-fail чтобы продолжить.", file=sys.stderr)
            break

    # 5. Output
    if args.json:
        import json
        json.dump([r.to_dict() for r in results], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_fixer_results(results)

    return fixer_exit_code(results)


def cmd_metrics(args: argparse.Namespace) -> int:
    """Print Prometheus textfile metrics для node_exporter."""
    from camera_bringup.metrics import collect
    sys.stdout.write(collect())
    return 0


def cmd_safe(args: argparse.Namespace) -> int:
    """F-isolation: enter/exit SAFE mode (quarantine)."""
    from camera_bringup.safe_mode import (
        enter_safe_mode,
        exit_safe_mode,
        is_safe_mode,
        safe_mode_info,
    )
    if args.action == "enter":
        info = enter_safe_mode(reason=args.reason or "manual")
        print(f"SAFE mode entered: {info}")
        return 0
    elif args.action == "exit":
        if exit_safe_mode():
            print("SAFE mode exited")
        else:
            print("not in SAFE mode")
        return 0
    elif args.action == "status":
        if is_safe_mode():
            import json
            print(json.dumps(safe_mode_info(), indent=2))
            return 0
        print("not in SAFE mode")
        return 1   # exit 1 если NOT safe (для cron checks)
    return 2


def cmd_list(args: argparse.Namespace) -> int:
    for name, fn in ALL_CHECKS:
        doc = (fn.__module__.split(".")[-1])
        # Берём первую строку docstring модуля
        try:
            mod = __import__(f"camera_bringup.checks.{doc}", fromlist=["check"])
            first_line = (mod.__doc__ or "").strip().splitlines()[0] if mod.__doc__ else ""
        except Exception:
            first_line = ""
        print(f"  {name:<18}  {first_line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="camera_bringup",
        description="Verify+configure camera stack at OS/driver layer (L0).",
    )
    parser.add_argument(
        "--instance",
        metavar="ID",
        default=None,
        help=(
            "Instance ID (e.g. cam-rgb, cam-rear). Default берётся из "
            "CAMERA_BRINGUP_INSTANCE env или 'cam-rgb'. Эквивалентно "
            "установке ENV перед запуском."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser(
        "verify",
        help="Read-only audit of all checks (no side effects).",
    )
    p_verify.add_argument(
        "--only",
        nargs="+",
        metavar="CHECK_ID",
        help="Run only specified checks (default: all).",
    )
    p_verify.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable.",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_apply = sub.add_parser(
        "apply",
        help="Apply fixers for checks in WARN/FAIL state (idempotent).",
    )
    p_apply.add_argument(
        "--only",
        nargs="+",
        metavar="CHECK_ID",
        help="Run only specified fixers (default: all available).",
    )
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions, do not execute.",
    )
    p_apply.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Continue with next fixer even if one fails (default: stop on first failure).",
    )
    p_apply.add_argument(
        "--yes-root",
        action="store_true",
        help="Suppress warning when fixers require root.",
    )
    p_apply.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable.",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_metrics = sub.add_parser(
        "metrics",
        help="Print Prometheus textfile metrics (for node_exporter textfile collector).",
    )
    p_metrics.set_defaults(func=cmd_metrics)

    p_safe = sub.add_parser(
        "safe",
        help="F-isolation: enter/exit SAFE mode (apply blocked while SAFE).",
    )
    p_safe.add_argument("action", choices=["enter", "exit", "status"])
    p_safe.add_argument("--reason", default=None, help="Reason для enter (логируется)")
    p_safe.set_defaults(func=cmd_safe)

    p_list = sub.add_parser("list", help="List available checks.")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    # --instance влияет на ENV до того как любой модуль импортирует spec.py.
    # Перед остальными импортами setup env; subsequent imports подхватят.
    if args.instance:
        import os
        os.environ["CAMERA_BRINGUP_INSTANCE"] = args.instance
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
