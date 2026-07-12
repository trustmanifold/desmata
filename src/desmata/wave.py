"""WAVE (WebAssembly Value Encoding) <-> Python values.

The invoker seam (:mod:`desmata.invoke`) speaks typed WIT values across the
component boundary; the ``wasmtime`` CLI encodes those values as WAVE
(``--invoke 'f(args...)'`` in, one WAVE value out). This module is the
canonical mapping between WAVE and the JSON-shaped Python values desmata
traffics in. It will outlive the CLI invoker: whatever runner executes a
component (wasmtime-py, a browser via jco), *this* is the value encoding a
``(cell_hash, function, args, result)`` quadruple is written in.

The canonical mapping (JSON value <-> WIT value):

* record       <-> object; keys are the WIT field names verbatim (kebab-case)
* list / tuple <-> array
* string       <-> string
* bool         <-> true / false
* u*/s*/f*     <-> number (bytes on the Python side encode to list<u8>)
* option       <-> the payload, or null for ``none`` (nested options are not
                   representable in JSON; none of desmata's surfaces use them)
* enum case    <-> the case name as a string

Only the subset desmata needs is implemented; unknown constructs raise
``ValueError`` rather than guessing.
"""

from typing import Any, Sequence

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r", "'": "'"}

_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_%")


def encode(value: Any) -> str:
    """Render a JSON-shaped Python value as a WAVE expression."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        body = "".join(_ESCAPES.get(ch, ch) for ch in value)
        return f'"{body}"'
    if isinstance(value, (bytes, bytearray)):
        return "[" + ", ".join(str(b) for b in value) + "]"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(encode(v) for v in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str) or not key or not set(key) <= _IDENT_CHARS:
                raise ValueError(f"record field name {key!r} is not a WIT identifier")
        body = ", ".join(f"{k}: {encode(v)}" for k, v in value.items())
        return "{" + body + "}"
    raise ValueError(f"cannot WAVE-encode {type(value).__name__}: {value!r}")


def encode_args(args: Sequence[Any]) -> str:
    """The WAVE argument list as it appears between the parentheses of
    ``--invoke 'f(...)'`` — and, verbatim, the X of an
    ``evaluates_to(C, F, X, Y)`` claim: a verifier re-invokes by wrapping
    this exact string back in ``F(...)``."""
    return ", ".join(encode(a) for a in args)


def decode(text: str) -> Any:
    """Parse a WAVE value (as printed by ``wasmtime --invoke``) into a
    JSON-shaped Python value. An empty string decodes to ``None`` (a function
    with no results prints nothing)."""
    parser = _Parser(text)
    parser.skip_ws()
    if parser.done():
        return None
    value = parser.value()
    parser.skip_ws()
    if not parser.done():
        raise ValueError(f"trailing input after WAVE value: {parser.rest()!r}")
    return value


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def done(self) -> bool:
        return self.pos >= len(self.text)

    def rest(self) -> str:
        return self.text[self.pos :]

    def peek(self) -> str:
        return self.text[self.pos] if not self.done() else ""

    def skip_ws(self) -> None:
        while not self.done() and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise ValueError(f"expected {ch!r} at {self.rest()!r}")
        self.pos += 1

    def value(self) -> Any:
        self.skip_ws()
        ch = self.peek()
        if ch == "[":
            return self.sequence("[", "]")
        if ch == "(":
            return self.sequence("(", ")")
        if ch == "{":
            return self.record()
        if ch == '"':
            return self.string()
        if ch == "'":
            return self.char()
        if ch.isdigit() or ch in "+-":
            return self.number()
        return self.keyword_or_enum()

    def sequence(self, open_ch: str, close_ch: str) -> list:
        self.expect(open_ch)
        items: list = []
        self.skip_ws()
        while self.peek() != close_ch:
            items.append(self.value())
            self.skip_ws()
            if self.peek() == ",":
                self.pos += 1
                self.skip_ws()
        self.expect(close_ch)
        return items

    def record(self) -> dict:
        self.expect("{")
        fields: dict = {}
        self.skip_ws()
        while self.peek() != "}":
            name = self.ident()
            self.skip_ws()
            self.expect(":")
            fields[name] = self.value()
            self.skip_ws()
            if self.peek() == ",":
                self.pos += 1
                self.skip_ws()
        self.expect("}")
        return fields

    def string(self) -> str:
        self.expect('"')
        out: list[str] = []
        while True:
            if self.done():
                raise ValueError("unterminated WAVE string")
            ch = self.text[self.pos]
            self.pos += 1
            if ch == '"':
                return "".join(out)
            if ch != "\\":
                out.append(ch)
                continue
            esc = self.text[self.pos]
            self.pos += 1
            if esc == "u":
                self.expect("{")
                end = self.text.index("}", self.pos)
                out.append(chr(int(self.text[self.pos : end], 16)))
                self.pos = end + 1
            elif esc in _UNESCAPES:
                out.append(_UNESCAPES[esc])
            else:
                raise ValueError(f"unknown escape \\{esc}")

    def char(self) -> str:
        # WAVE chars surface as one-character Python strings.
        self.expect("'")
        ch = self.text[self.pos]
        self.pos += 1
        if ch == "\\":
            esc = self.text[self.pos]
            self.pos += 1
            ch = _UNESCAPES.get(esc, esc)
        self.expect("'")
        return ch

    def number(self) -> int | float:
        start = self.pos
        if self.peek() in "+-":
            self.pos += 1
        while not self.done() and (self.text[self.pos].isdigit() or self.text[self.pos] in ".eE+-"):
            # '+'/'-' only continue a number after an exponent marker
            if self.text[self.pos] in "+-" and self.text[self.pos - 1] not in "eE":
                break
            self.pos += 1
        token = self.text[start : self.pos]
        return float(token) if any(c in token for c in ".eE") else int(token)

    def ident(self) -> str:
        start = self.pos
        while not self.done() and self.text[self.pos] in _IDENT_CHARS:
            self.pos += 1
        if start == self.pos:
            raise ValueError(f"expected identifier at {self.rest()!r}")
        return self.text[start : self.pos]

    def keyword_or_enum(self) -> Any:
        word = self.ident()
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "none":
            return None
        if word in ("inf", "nan"):
            return float(word)
        self.skip_ws()
        if self.peek() == "(":
            payload = self.sequence("(", ")")
            if word == "some":
                if len(payload) != 1:
                    raise ValueError(f"some(...) takes one payload, got {payload!r}")
                return payload[0]
            raise ValueError(f"variant case {word!r} is not part of the canonical mapping")
        return word  # an enum case
