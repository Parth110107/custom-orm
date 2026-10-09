"""
orm/migrations/cli.py
=====================
Entry point for the ``orm-migrate`` console script.

Commands
--------
makemigrations:
    Detect differences between model definitions and live DB, then write
    a timestamped .sql migration file.

migrate:
    Apply all .sql files in the migrations directory to the DB in order.

Usage
-----
::

    # Detect and write a migration file
    orm-migrate makemigrations --url postgresql://... --models myapp.models

    # Apply all pending migration files
    orm-migrate migrate --url postgresql://... --dir ./migrations

Why a CLI instead of a Python API?
-----------------------------------
The CLI is the user-facing interface for migration management.  The Python
API (SchemaDetector, MigrationWriter, MigrationApplier) is the internal
interface that the CLI calls.  Keeping them separate means the CLI can be
swapped out (e.g., for a Click-based CLI in v2) without touching the core.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any


def _load_models(model_paths: list[str]) -> list[Any]:
    """
    Import model classes from dotted paths like ``myapp.models.Post``.

    The user passes ``--models myapp.models.Author myapp.models.Post`` and we
    import each class dynamically.

    Parameters
    ----------
    model_paths:
        List of dotted module.ClassName strings.

    Returns
    -------
    List of Model subclasses.
    """
    models = []
    for path in model_paths:
        module_path, cls_name = path.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)
        models.append(cls)
    return models


def _load_all_models_from_module(module_path: str) -> list[Any]:
    """
    Import all Model subclasses from a module.

    Useful when the user passes ``--module myapp.models`` and wants all
    models in that module included.
    """
    from orm.models import Model

    mod = importlib.import_module(module_path)
    return [
        obj
        for obj in vars(mod).values()
        if isinstance(obj, type) and issubclass(obj, Model) and obj is not Model
    ]


def cmd_makemigrations(args: argparse.Namespace) -> int:
    """
    Detect schema diff and write a migration file.

    Returns exit code (0 = success, 1 = error).
    """
    from orm.db import Database
    from orm.migrations.detector import SchemaDetector
    from orm.migrations.writer import MigrationWriter

    db = Database(args.url, max_conn=1)

    # Load models
    if args.module:
        models = _load_all_models_from_module(args.module)
    elif args.models:
        models = _load_models(args.models)
    else:
        print("Error: specify --module or --models", file=sys.stderr)
        return 1

    if not models:
        print("No models found.", file=sys.stderr)
        return 1

    db.register(*models)

    detector = SchemaDetector(db, models)
    diff = detector.detect()

    if not diff.has_changes:
        print("No schema changes detected. Nothing to do.")
        return 0

    writer = MigrationWriter(db, migration_dir=args.dir)
    filepath = writer.write_migration_file(diff)

    if filepath:
        print(f"Migration file written: {filepath}")
        if diff.new_tables:
            print(f"  New tables: {[m._meta.table_name for m in diff.new_tables]}")
        if diff.new_columns:
            for table, cols in diff.new_columns.items():
                print(f"  New columns on {table}: {[c for c, _ in cols]}")
        if diff.skipped:
            print(
                f"\nWarning: {len(diff.skipped)} destructive change(s) skipped "
                "(see warnings above). Apply manually."
            )

    db.close_all()
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """
    Apply all pending migration files to the database with state tracking.

    Returns exit code (0 = success, 1 = error).
    """
    import os
    from orm.db import Database
    from orm.migrations.writer import MigrationApplier

    db = Database(args.url, max_conn=1)
    applier = MigrationApplier(db, migration_dir=args.dir)

    if getattr(args, "status", False):
        applied = applier.history.get_applied() if applier.history else []
        pending = applier.pending_files()
        print(f"Applied migrations ({len(applied)}):")
        for f in applied:
            print(f"  [x] {f}")
        print(f"Pending migrations ({len(pending)}):")
        for f in pending:
            print(f"  [ ] {os.path.basename(f)}")
        db.close_all()
        return 0

    if getattr(args, "rollback", False):
        rolled = applier.rollback_last()
        if rolled:
            print(f"Rolled back record for migration: {rolled}")
        else:
            print("No migrations available to roll back.")
        db.close_all()
        return 0

    pending = applier.pending_files()
    if not pending:
        print(f"No pending migration files found in {args.dir!r}.")
        db.close_all()
        return 0

    dry = getattr(args, "dry_run", False)
    prefix = "[DRY RUN] " if dry else ""
    print(f"{prefix}Applying {len(pending)} migration file(s)...")
    try:
        total = applier.apply_all(dry_run=dry)
        print(f"{prefix}Done. {total} statement(s) processed.")
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        db.close_all()
        return 1

    db.close_all()
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``orm-migrate`` console script."""
    parser = argparse.ArgumentParser(
        prog="orm-migrate",
        description="custom-orm migration tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- makemigrations ---
    mk = sub.add_parser(
        "makemigrations",
        help="Detect schema diff and write a .sql migration file.",
    )
    mk.add_argument("--url", required=True, help="Database DSN")
    mk.add_argument(
        "--module",
        default=None,
        help="Python module path containing all models (e.g. myapp.models)",
    )
    mk.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Dotted paths to model classes (e.g. myapp.models.Post)",
    )
    mk.add_argument(
        "--dir",
        default="migrations",
        help="Directory to write migration files (default: ./migrations)",
    )

    # --- migrate ---
    mg = sub.add_parser(
        "migrate",
        help="Apply all pending .sql migration files.",
    )
    mg.add_argument("--url", required=True, help="Database DSN")
    mg.add_argument(
        "--dir",
        default="migrations",
        help="Directory containing migration files (default: ./migrations)",
    )
    mg.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without executing statements or recording history",
    )
    mg.add_argument(
        "--status",
        action="store_true",
        help="List applied and pending migrations",
    )
    mg.add_argument(
        "--rollback",
        action="store_true",
        help="Revert history record for the most recently applied migration",
    )

    args = parser.parse_args(argv)

    if args.command == "makemigrations":
        sys.exit(cmd_makemigrations(args))
    elif args.command == "migrate":
        sys.exit(cmd_migrate(args))


if __name__ == "__main__":
    main()
