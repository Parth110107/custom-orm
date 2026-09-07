"""
orm/exceptions.py
-----------------
Defines the public exception hierarchy for custom-orm.

All ORM-level errors derive from ``OrmError`` so callers can catch the whole
family with a single ``except OrmError`` clause while still being able to
handle specific sub-classes when they need finer control.

When wrapping a driver exception we always chain it via ``raise X(...) from
original`` so that the full traceback is preserved for debugging.  Each
wrapper class documents the exact scenario in which it is raised.
"""

from __future__ import annotations


class OrmError(Exception):
    """
    Root of the custom-orm exception hierarchy.

    Every exception raised by the ORM is a subclass of ``OrmError``.
    This lets application code do a broad ``except OrmError`` to catch any
    ORM-related problem without accidentally swallowing unrelated errors.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    """


class DoesNotExist(OrmError):
    """
    Raised by ``QuerySet.get()`` when the filter matches zero rows.

    Using a dedicated exception (rather than returning ``None``) forces the
    caller to explicitly handle the "not found" case, which prevents silent
    bugs caused by operating on a ``None`` value later in the call stack.

    Example
    -------
    >>> try:
    ...     user = User.objects.get(id=999)
    ... except DoesNotExist:
    ...     print("No such user")
    """


class MultipleObjectsReturned(OrmError):
    """
    Raised by ``QuerySet.get()`` when the filter matches more than one row.

    ``get()`` is a *singular* lookup — it promises exactly one result.
    Returning the first row silently would hide data-integrity problems (e.g.,
    a UNIQUE constraint that was never added).  Raising here makes the problem
    immediately visible.

    Example
    -------
    >>> # If two rows share the same email this raises instead of silently
    >>> # returning one of them.
    >>> user = User.objects.get(email="dup@example.com")
    """


class IntegrityError(OrmError):
    """
    Wraps database-level integrity violations.

    Covers UNIQUE constraint failures, NOT NULL violations, and foreign-key
    constraint violations.  The original driver exception is always attached as
    ``__cause__`` so the DB error message is not lost.

    We re-raise as ``IntegrityError`` (rather than letting the raw driver
    exception bubble up) so application code never has to import
    ``psycopg.errors`` or ``pymysql.err`` — it only needs to know about the
    ORM's own exception types.

    Example
    -------
    >>> try:
    ...     User.objects.create(email="taken@example.com")
    ... except IntegrityError as exc:
    ...     print(f"DB said: {exc.__cause__}")
    """


class ConnectionError(OrmError):
    """
    Raised when the ORM cannot open or acquire a database connection.

    Covers TCP-level failures, authentication errors, pool exhaustion, and
    any other condition that prevents establishing a usable connection.  The
    original driver exception is chained as ``__cause__``.

    Naming note: although Python's built-in ``ConnectionError`` exists, we
    shadow it intentionally inside the ``orm`` namespace.  Callers who import
    from ``orm.exceptions`` get our version; code outside the ORM still sees
    the built-in.
    """


class MigrationError(OrmError):
    """
    Raised during schema migration detection or application.

    This covers situations such as:
    - The migration state table cannot be created or queried.
    - A migration script fails to apply.
    - Detected schema drift cannot be reconciled automatically.

    Wrapping migration failures in a dedicated type lets deployment tooling
    catch only migration problems without also swallowing unrelated
    ``OrmError`` subclasses.
    """


class FieldError(OrmError):
    """
    Raised for invalid field definitions or invalid lookup expressions.

    Examples of when this is raised:
    - ``CharField`` is declared without ``max_length``.
    - A ``filter()`` keyword references a field name that does not exist on
      the model.
    - A field receives a value of the wrong Python type during validation.

    Catching ``FieldError`` separately from ``IntegrityError`` matters because
    ``FieldError`` always indicates a *programming* mistake (wrong model or
    query definition), whereas ``IntegrityError`` can be a *runtime* data
    problem that the application may want to handle gracefully.
    """
