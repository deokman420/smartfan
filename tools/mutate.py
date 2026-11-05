"""Mutation testing for the fan controller's logic.

Deliberately break one decision at a time and see whether the suite
notices. A mutant that survives is a line the tests do not actually
constrain -- which is where today's bugs were living.

    python mutate.py <target.py> <suite.py> [--limit N]

Prints every survivor with its location and the exact change, so each one
can be judged by hand. Equivalent mutants (changes that cannot alter
behaviour) are expected and are the reviewer's job to discard.
"""
import ast
import copy
import io
import os
import shutil
import subprocess
import sys
import time

# Python validates a cached .pyc on (mtime, size) with MTIME IN WHOLE
# SECONDS. Mutants are written to the same path, within the same second, and
# are very often exactly the same LENGTH -- "==" for "!=", "0" for "1". So
# the interpreter reran the PREVIOUS mutant's bytecode and the current one
# was recorded as surviving. That under-counts kills, and for a tool whose
# entire output is a list of things your tests fail to catch, inventing
# extra entries is the worst direction to be wrong in.
_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

CMP = {
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
}
CMP_NAME = {
    ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
    ast.Eq: "==", ast.NotEq: "!=", ast.Is: "is", ast.IsNot: "is not",
    ast.In: "in", ast.NotIn: "not in",
}


class Collect(ast.NodeVisitor):
    """Every place a single decision could be flipped."""

    def __init__(self):
        self.sites = []     # (kind, node, extra)

    def visit_Compare(self, node):
        for i, op in enumerate(node.ops):
            if type(op) in CMP:
                self.sites.append(("cmp", node, i))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.sites.append(("bool", node, None))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.sites.append(("not", node, None))
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            self.sites.append(("boolconst", node, None))
        elif isinstance(node.value, int) and abs(node.value) < 10 ** 7:
            self.sites.append(("num", node, None))
        self.generic_visit(node)


def describe(kind, node, extra):
    if kind == "cmp":
        op = node.ops[extra]
        return "%s -> %s" % (CMP_NAME[type(op)], CMP_NAME[CMP[type(op)]])
    if kind == "bool":
        return "and -> or" if isinstance(node.op, ast.And) else "or -> and"
    if kind == "not":
        return "drop `not`"
    if kind == "boolconst":
        return "%s -> %s" % (node.value, not node.value)
    if kind == "num":
        return "%d -> %d" % (node.value, node.value + 1)
    return "?"


def apply(tree, kind, path, extra):
    """Re-find the node by its path index in a fresh tree and mutate it."""
    c = Collect()
    c.visit(tree)
    k2, node, e2 = c.sites[path]
    assert k2 == kind
    if kind == "cmp":
        node.ops[extra] = CMP[type(node.ops[extra])]()
    elif kind == "bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "not":
        return ("replace_not", node)
    elif kind == "boolconst":
        node.value = not node.value
    elif kind == "num":
        node.value = node.value + 1
    return None


class DropNot(ast.NodeTransformer):
    def __init__(self, target):
        self.target = target

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if node is self.target:
            return node.operand
        return node


def main():
    argv = sys.argv[1:]
    limit = None
    if "--limit" in argv:
        i = argv.index("--limit")
        limit = int(argv[i + 1])
        del argv[i:i + 2]
    target, suites = argv[0], argv[1:]

    raw = io.open(target, "rb").read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    src = raw.decode("utf-8")
    if nl == "\r\n":
        src = src.replace(nl, "\n")
    base = ast.parse(src)
    sites = []
    c = Collect()
    c.visit(base)
    for i, (kind, node, extra) in enumerate(c.sites):
        sites.append((i, kind, extra, getattr(node, "lineno", 0),
                      describe(kind, node, extra)))
    if limit:
        sites = sites[:limit]

    # A cache left by an earlier run is keyed on the pristine file and
    # would be served to the first mutant.
    shutil.rmtree(os.path.join(
        os.path.dirname(os.path.abspath(target)) or ".", "__pycache__"),
        ignore_errors=True)
    lines = src.split("\n")
    backup = src
    survivors, killed, broken = [], 0, 0
    t0 = time.time()
    try:
        for n, (idx, kind, extra, lineno, desc) in enumerate(sites):
            tree = ast.parse(src)
            res = apply(tree, kind, idx, extra)
            if res and res[0] == "replace_not":
                tree = DropNot(res[1]).visit(tree)
                ast.fix_missing_locations(tree)
            try:
                mutated = ast.unparse(tree)
            except Exception:
                broken += 1
                continue
            io.open(target, "w", encoding="utf-8", newline=nl).write(mutated)
            # Killed if ANY suite notices. A module covered by several
            # suites is under-measured by asking only one of them.
            alive = True
            for s in suites:
                r = subprocess.run([sys.executable, "-B", s], env=_ENV,
                                   capture_output=True, text=True, timeout=180)
                if r.returncode != 0:
                    alive = False
                    break
            if alive:
                code = lines[lineno - 1].strip() if lineno <= len(lines) else "?"
                survivors.append((lineno, desc, code))
            else:
                killed += 1
            if (n + 1) % 50 == 0:
                print("  ...%d/%d  (%d survived)  %.0fs"
                      % (n + 1, len(sites), len(survivors), time.time() - t0),
                      flush=True)
    finally:
        io.open(target, "w", encoding="utf-8", newline=nl).write(backup)

    print("\n%s against %s" % (target, ", ".join(suites)))
    print("%d mutants: %d killed, %d survived, %d unparseable"
          % (len(sites), killed, len(survivors), broken))
    print("\nSURVIVORS (each is a decision the suite does not pin):\n")
    for lineno, desc, code in survivors:
        print("  %s:%-5d %-18s %s" % (target, lineno, desc, code[:88]))


if __name__ == "__main__":
    main()
