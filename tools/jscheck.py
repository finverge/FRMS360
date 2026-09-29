"""A dependency-free structural check for the console's static assets.

This is NOT a JavaScript parser. It is a scanner for the specific class of defect that has
actually bitten this project: a string literal broken across a newline, which silently
invalidates the whole file so the console renders nothing and the browser is the only thing
that notices.

It tracks string / template / comment / regex state and reports:
  * a raw newline inside a '...' or "..." literal   (the bug that shipped)
  * an unterminated string, template or block comment
  * unbalanced (), [] or {}

Node is not available in this environment, so a real parser is not an option; this catches
the failure mode that matters without adding a toolchain.

Usage:  python tools/jscheck.py [paths...]
"""
import sys
from pathlib import Path

PAIRS = {")": "(", "]": "[", "}": "{"}
OPENERS = set(PAIRS.values())
# Characters after which a '/' starts a regex rather than a division.
REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {""}


def check_source(src: str, name: str = "<js>") -> list[str]:
    errors: list[str] = []
    stack: list[tuple[str, int]] = []
    i, line = 0, 1
    n = len(src)
    prev_significant = ""

    while i < n:
        ch = src[i]

        if ch == "\n":
            line += 1
            i += 1
            continue

        # comments
        if ch == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                while i < n and src[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                end = src.find("*/", i + 2)
                if end == -1:
                    errors.append(f"{name}:{line}: unterminated block comment")
                    return errors
                line += src.count("\n", i, end)
                i = end + 2
                continue
            # regex literal (only where a value may start)
            if prev_significant in REGEX_PRECEDERS:
                j, closed = i + 1, False
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "\n":
                        break
                    if src[j] == "/":
                        closed = True
                        break
                    j += 1
                if closed:
                    i = j + 1
                    prev_significant = "/"
                    continue

        # strings
        if ch in "\"'":
            quote, j = ch, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "\n":
                    errors.append(
                        f"{name}:{line}: unterminated string literal - a raw newline "
                        f"inside {quote}...{quote} (use \\n)")
                    return errors
                if src[j] == quote:
                    break
                j += 1
            if j >= n:
                errors.append(f"{name}:{line}: unterminated string literal")
                return errors
            i = j + 1
            prev_significant = quote
            continue

        # template literals (newlines are legal; ${} may nest)
        if ch == "`":
            j, depth = i + 1, 0
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "\n":
                    line += 1
                elif src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                    depth += 1
                    j += 1
                elif src[j] == "}" and depth:
                    depth -= 1
                elif src[j] == "`" and depth == 0:
                    break
                j += 1
            if j >= n:
                errors.append(f"{name}:{line}: unterminated template literal")
                return errors
            i = j + 1
            prev_significant = "`"
            continue

        # brackets
        if ch in OPENERS:
            stack.append((ch, line))
        elif ch in PAIRS:
            if not stack:
                errors.append(f"{name}:{line}: unexpected closing '{ch}'")
            elif stack[-1][0] != PAIRS[ch]:
                open_ch, open_line = stack[-1]
                errors.append(
                    f"{name}:{line}: '{ch}' closes '{open_ch}' opened on line {open_line}")
                stack.pop()
            else:
                stack.pop()

        if not ch.isspace():
            prev_significant = ch
        i += 1

    for open_ch, open_line in stack:
        errors.append(f"{name}:{open_line}: unclosed '{open_ch}'")
    return errors


def check_file(path: Path) -> list[str]:
    return check_source(path.read_text(encoding="utf-8"), str(path))


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [
        Path(__file__).resolve().parents[1] / "services/gateway/app/static"]
    files: list[Path] = []
    for t in targets:
        files.extend(sorted(t.rglob("*.js")) if t.is_dir() else [t])
    # The vendored chart library is third-party and minified; not ours to lint.
    files = [f for f in files if "vendor" not in f.parts]

    failures = 0
    for f in files:
        errs = check_file(f)
        print(f"{'FAIL' if errs else 'ok  '} {f}")
        for e in errs:
            print(f"      {e}")
        failures += bool(errs)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
