"""{{ var }} interpolation plus a tiny safe expression evaluator.

The condition language supports numbers, strings, true/false/null, arithmetic
(+ - * / %), comparisons (== != < <= > >= in) and boolean logic (and or not)
with parentheses. Values come from the run context: variables.<name>,
steps.<step_id>.output.<key>, steps.<step_id>.exit_code, steps.<step_id>.attempts,
or this.item / this.index inside a foreach loop.
"""
from __future__ import annotations

import re
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class InterpolationError(Exception):
    pass


class ConditionError(Exception):
    pass


def _split_path(path: str) -> list[str]:
    return [s for s in re.split(r"[\[\].]+", path) if s]


def resolve_path(context: dict, path: str) -> Any:
    """Walk a dotted path like steps.checkout.output.stdout through the context."""
    cur: Any = context
    for seg in _split_path(path):
        if isinstance(cur, dict):
            if seg in cur:
                cur = cur[seg]
                continue
            raise KeyError(path)
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
                continue
            except (ValueError, IndexError):
                raise KeyError(path) from None
        raise KeyError(path)
    return cur


def interpolate(value: Any, context: dict) -> Any:
    """Replace every {{ path }} placeholder in strings (recursively for dicts/lists)."""
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            path = match.group(1).strip()
            try:
                resolved = resolve_path(context, path)
            except KeyError:
                raise InterpolationError(f"cannot resolve '{path}' (not in context)") from None
            return _to_scalar(resolved)

        return PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, context) for v in value]
    return value


def _to_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value)
    return str(value)


# ---- tiny recursive-descent parser over a safe grammar -----------------

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<number>\d+\.\d+|\d+)
      | (?P<string>'[^']*'|"[^"]*")
      | (?P<kw>and|or|not|in|true|false|null)
      | (?P<op>==|!=|<=|>=|[<>+\-*/%()])
      | (?P<ident>[A-Za-z_][A-Za-z0-9_.\[\]]*)
    )
    """,
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            rest = text[pos:].strip()
            raise ConditionError(f"unexpected token near: {rest[:20]!r}")
        kind = m.lastgroup
        val = m.group(kind)  # leading whitespace is consumed by \s*
        if kind == "number":
            tokens.append(("number", float(val) if "." in val else int(val)))
        elif kind == "string":
            tokens.append(("string", val[1:-1]))
        elif kind == "kw":
            tokens.append(("kw", val))
        elif kind == "op":
            tokens.append(("op", val))
        else:
            tokens.append(("ident", val))
        pos = m.end()
    tokens.append(("eof", None))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]], context: dict):
        self.tokens = tokens
        self.pos = 0
        self.context = context

    def peek(self) -> tuple[str, Any]:
        return self.tokens[self.pos]

    def next(self) -> tuple[str, Any]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, op: str) -> None:
        kind, val = self.next()
        if kind != "op" or val != op:
            raise ConditionError(f"expected '{op}'")

    def parse(self) -> Any:
        return self.parse_or()

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self.peek() == ("kw", "or"):
            self.next()
            right = self.parse_and()
            left = bool(left) or bool(right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_not()
        while self.peek() == ("kw", "and"):
            self.next()
            right = self.parse_not()
            left = bool(left) and bool(right)
        return left

    def parse_not(self) -> Any:
        if self.peek() == ("kw", "not"):
            self.next()
            return not bool(self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Any:
        left = self.parse_additive()
        kind, val = self.peek()
        if kind == "op" and val in ("==", "!=", "<", "<=", ">", ">="):
            self.next()
            right = self.parse_additive()
            return _compare(val, left, right)
        if kind == "kw" and val == "in":
            self.next()
            right = self.parse_additive()
            try:
                return left in right
            except TypeError:
                return False
        return left

    def parse_additive(self) -> Any:
        left = self.parse_term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            right = self.parse_term()
            left = _arith(op, left, right)
        return left

    def parse_term(self) -> Any:
        left = self.parse_factor()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "%"):
            op = self.next()[1]
            right = self.parse_factor()
            left = _arith(op, left, right)
        return left

    def parse_factor(self) -> Any:
        kind, val = self.peek()
        if kind == "op" and val in ("-", "+"):
            self.next()
            node = self.parse_factor()
            return -node if val == "-" else node
        if kind == "op" and val == "(":
            self.next()
            node = self.parse_or()
            self.expect_op(")")
            return node
        if kind == "number":
            self.next()
            return val
        if kind == "string":
            self.next()
            return val
        if kind == "kw":
            self.next()
            if val == "true":
                return True
            if val == "false":
                return False
            if val == "null":
                return None
            raise ConditionError(f"unexpected keyword '{val}'")
        if kind == "ident":
            self.next()
            try:
                return resolve_path(self.context, val)
            except KeyError:
                raise ConditionError(f"unknown identifier '{val}'") from None
        raise ConditionError(f"unexpected token {kind}:{val}")


def _arith(op: str, a: Any, b: Any) -> Any:
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "%":
            return a % b
    except (TypeError, ZeroDivisionError) as exc:
        raise ConditionError(f"arithmetic error in '{a} {op} {b}': {exc}") from exc
    raise ConditionError(f"unknown operator {op}")


def _compare(op: str, a: Any, b: Any) -> bool:
    try:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
    except TypeError:
        return False
    raise ConditionError(f"unknown comparison {op}")


def evaluate_condition(expression: str, context: dict) -> bool:
    """Evaluate a condition string against the run context, return a bool."""
    expr = PLACEHOLDER_RE.sub(
        lambda m: _to_scalar(resolve_path(context, m.group(1).strip())), expression
    )
    try:
        tokens = _tokenize(expr)
        result = _Parser(tokens, context).parse()
    except ConditionError:
        raise
    except Exception as exc:  # defensive: anything else becomes a condition error
        raise ConditionError(f"failed to evaluate condition: {exc}") from exc
    return bool(result)