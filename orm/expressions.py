"""
orm/expressions.py
==================
AST expression nodes for advanced queries, boolean combinations,
column arithmetic, and database aggregations.

Includes:
  - Q: Composable boolean expression tree (AND, OR, NOT) with nested support.
  - F: In-database column references supporting arithmetic (+, -, *, /).
  - Aggregate functions: Count, Sum, Avg, Max, Min.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from orm.exceptions import FieldError

if TYPE_CHECKING:
    from orm.dialects.base import DialectBase
    from orm.models import Model


class Q:
    """
    Encapsulates a boolean query condition or composite expression tree.

    Supports binary operations:
        q1 & q2   (AND)
        q1 | q2   (OR)
        ~q1       (NOT)

    Examples
    --------
    ::

        Q(name="Alice") | Q(name="Bob")
        Q(age__gte=18) & ~Q(status="banned")
        Q(Q(role="admin") | Q(role="superuser"), is_active=True)
    """

    AND = "AND"
    OR = "OR"

    def __init__(self, *args: Q, **kwargs: Any) -> None:
        self.connector: str = self.AND
        self.negated: bool = False
        # Children can be either (lookup_key, value) tuples or nested Q instances
        self.children: list[tuple[str, Any] | Q] = []

        for arg in args:
            if isinstance(arg, Q):
                self.children.append(arg)
            else:
                raise TypeError(f"Positional arguments to Q must be Q instances, got {type(arg).__name__}")

        for k, v in kwargs.items():
            self.children.append((k, v))

    def __and__(self, other: Q) -> Q:
        return self._combine(other, self.AND)

    def __or__(self, other: Q) -> Q:
        return self._combine(other, self.OR)

    def __invert__(self) -> Q:
        clone = self._clone()
        clone.negated = not clone.negated
        return clone

    def _clone(self) -> Q:
        clone = Q()
        clone.connector = self.connector
        clone.negated = self.negated
        clone.children = []
        for child in self.children:
            if isinstance(child, Q):
                clone.children.append(child._clone())
            else:
                clone.children.append(child)
        return clone

    def _combine(self, other: Q, connector: str) -> Q:
        if not isinstance(other, Q):
            raise TypeError(f"Cannot combine Q with {type(other).__name__}")

        # Empty Q combinations
        if not self.children:
            return other._clone()
        if not other.children:
            return self._clone()

        result = Q()
        result.connector = connector
        result.children = [self._clone(), other._clone()]
        return result

    def compile(
        self,
        model: type[Model],
        dialect: DialectBase,
    ) -> tuple[str, list[Any]]:
        """
        Recursively compile the Q node into a parameterized SQL fragment and param list.
        """
        from orm.queryset import _build_condition, _parse_lookup

        if not self.children:
            return "", []

        placeholder = dialect.placeholder()
        meta = getattr(model, "_meta", None)
        parts: list[str] = []
        params: list[Any] = []

        for child in self.children:
            if isinstance(child, Q):
                sub_sql, sub_params = child.compile(model, dialect)
                if sub_sql:
                    parts.append(f"({sub_sql})")
                    params.extend(sub_params)
            else:
                key, value = child
                col_name, lookup = _parse_lookup(key)
                if meta and hasattr(meta, "column_to_field"):
                    # Resolve foreign key or direct field
                    if col_name in meta.fields:
                        col_name = meta.fields[col_name].column_name or col_name
                    elif f"{col_name}_id" in meta.fields:
                        col_name = f"{col_name}_id"

                frag, vals = _build_condition(col_name, lookup, value, placeholder)
                parts.append(frag)
                params.extend(vals)

        if not parts:
            return "", []

        sql = f" {self.connector} ".join(parts)
        if len(parts) > 1:
            sql = f"({sql})"

        if self.negated:
            sql = f"NOT ({sql})"

        return sql, params

    def __repr__(self) -> str:
        prefix = "NOT " if self.negated else ""
        return f"<{prefix}Q: {self.children} (conn={self.connector})>"


class F:
    """
    Represents the value of a database column.

    Enables atomic database-side calculations and column-to-column comparisons
    without transferring values to the Python interpreter.

    Examples
    --------
    ::

        Post.objects.update(views=F("views") + 1)
        Product.objects.filter(price__lt=F("regular_price"))
    """

    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.op: str | None = None
        self.other: Any = None

    def __add__(self, other: Any) -> F:
        return self._op(self.ADD, other)

    def __sub__(self, other: Any) -> F:
        return self._op(self.SUB, other)

    def __mul__(self, other: Any) -> F:
        return self._op(self.MUL, other)

    def __truediv__(self, other: Any) -> F:
        return self._op(self.DIV, other)

    def _op(self, op: str, other: Any) -> F:
        clone = F(self.name)
        clone.op = op
        clone.other = other
        return clone

    def compile(
        self,
        model: type[Model],
        dialect: DialectBase,
    ) -> tuple[str, list[Any]]:
        """Compile the F-expression into SQL and parameters."""
        meta = getattr(model, "_meta", None)
        col = self.name
        if meta and hasattr(meta, "fields"):
            if col in meta.fields:
                col = meta.fields[col].column_name or col
            elif f"{col}_id" in meta.fields:
                col = f"{col}_id"

        if self.op is None:
            return col, []

        # If operating with another F object
        if isinstance(self.other, F):
            other_sql, other_params = self.other.compile(model, dialect)
            return f"({col} {self.op} {other_sql})", other_params

        # Operating with a constant scalar value
        placeholder = dialect.placeholder()
        return f"({col} {self.op} {placeholder})", [self.other]

    def __repr__(self) -> str:
        if self.op:
            return f"<F: {self.name} {self.op} {self.other!r}>"
        return f"<F: {self.name}>"


class Aggregate:
    """
    Base class for SQL aggregate functions (COUNT, SUM, AVG, MAX, MIN).
    """

    function_name: str = ""

    def __init__(self, expression: str | F = "*", distinct: bool = False) -> None:
        self.expression: str | F = expression
        self.distinct: bool = distinct

    def compile(
        self,
        model: type[Model],
        dialect: DialectBase,
    ) -> tuple[str, list[Any]]:
        """Compile aggregate function to SQL fragment."""
        meta = getattr(model, "_meta", None)
        if isinstance(self.expression, F):
            expr_sql, params = self.expression.compile(model, dialect)
        elif self.expression == "*":
            expr_sql, params = "*", []
        else:
            col = str(self.expression)
            if meta and hasattr(meta, "fields"):
                if col in meta.fields:
                    col = meta.fields[col].column_name or col
                elif f"{col}_id" in meta.fields:
                    col = f"{col}_id"
            expr_sql, params = col, []

        distinct_kw = "DISTINCT " if self.distinct else ""
        sql = f"{self.function_name}({distinct_kw}{expr_sql})"
        return sql, params

    def __repr__(self) -> str:
        dist = "DISTINCT " if self.distinct else ""
        return f"<{self.function_name}({dist}{self.expression})>"


class Count(Aggregate):
    function_name = "COUNT"


class Sum(Aggregate):
    function_name = "SUM"


class Avg(Aggregate):
    function_name = "AVG"


class Max(Aggregate):
    function_name = "MAX"


class Min(Aggregate):
    function_name = "MIN"
