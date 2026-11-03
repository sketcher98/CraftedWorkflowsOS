#!/usr/bin/env python3
"""config_edit.py — deterministic YAML config editing helper.

Used by the bootstrap's configure-hermes.sh and apply-config-change.sh.
Never prints secret values; operates only on files it is given.

Commands:
  validate --file F            exit 0 if F parses as YAML
  render --template T [--secrets S.json] --out O
                               render template T to O; inject secrets from S
                               (flat dotted-path -> value/JSON map, optional);
                               strip keys starting with '_template_'
  set --file F --path A.B.C [--value V | --value-json V]
                               set one leaf value (in-place)
  patch --file F --patch P.json
                               apply multiple path->value changes (in-place)
  paths --file F               print all leaf dotted paths
"""
import argparse, json, os, sys
import yaml

def parse_path(path):
    """'a.b[0].c' -> list of parts (a, b, 0, c)."""
    parts = []
    for tok in path.split("."):
        if "[" in tok:
            head, rest = tok.split("[", 1)
            if head:
                parts.append(head)
            for idx in rest.rstrip("]").split("]["):
                parts.append(int(idx))
        else:
            parts.append(tok)
    return parts

def get_path(node, parts):
    cur = node
    for p in parts:
        if isinstance(cur, dict):
            cur = cur[p]
        elif isinstance(cur, list):
            cur = cur[int(p)]
        else:
            raise KeyError(path)
    return cur

def set_path(node, parts, value):
    cur = node
    for p in parts[:-1]:
        if isinstance(cur, dict):
            cur = cur.setdefault(p, {})
        elif isinstance(cur, list):
            cur = cur[int(p)]
        else:
            raise ValueError(f"cannot descend through non-container at {p}")
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list):
        cur[int(last)] = value
    else:
        raise ValueError(f"cannot set leaf on non-container at {last}")

def leaf_paths(node, prefix=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            out += leaf_paths(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += leaf_paths(v, f"{prefix}[{i}]")
    else:
        out.append(prefix)
    return out

def coerce(value):
    if isinstance(value, (dict, list, bool, int, float)) or value is None:
        return value
    s = value.strip()
    if s in ("true", "True"): return True
    if s in ("false", "False"): return False
    if s in ("null", "None", "~"): return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return value

def load(fn):
    with open(fn) as f:
        return yaml.safe_load(f)

def dump(fn, data):
    with open(fn, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "render", "set", "patch", "paths"])
    ap.add_argument("--file")
    ap.add_argument("--template")
    ap.add_argument("--secrets")
    ap.add_argument("--out")
    ap.add_argument("--path")
    ap.add_argument("--value")
    ap.add_argument("--value-json")
    ap.add_argument("--patch")
    args = ap.parse_args()

    if args.cmd == "validate":
        if not args.file:
            ap.error("--file required")
        try:
            data = load(args.file)
        except Exception as e:
            print(f"INVALID: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(data, dict):
            print("INVALID: top-level not a mapping", file=sys.stderr)
            sys.exit(1)
        print("OK")
        sys.exit(0)

    if args.cmd == "render":
        if not (args.template and args.out):
            ap.error("--template and --out required")
        data = load(args.template)
        # strip template-notes keys
        for k in list(data):
            if k.startswith("_template_"):
                del data[k]
        if args.secrets:
            if not os.path.exists(args.secrets):
                print(f"secrets bundle missing: {args.secrets}", file=sys.stderr)
                sys.exit(1)
            secrets = json.load(open(args.secrets))
            cfg_sec = secrets.get("config") or {}
            for path, value in cfg_sec.items():
                try:
                    set_path(data, parse_path(path), value)
                except Exception as e:
                    print(f"secret path error {path}: {e}", file=sys.stderr)
                    sys.exit(1)
        dump(args.out, data)
        print(f"rendered {args.out}")
        sys.exit(0)

    if args.cmd == "set":
        if not (args.file and args.path):
            ap.error("--file and --path required")
        if args.value_json is not None:
            value = json.loads(args.value_json)
        elif args.value is not None:
            value = coerce(args.value)
        else:
            ap.error("--value or --value-json required")
        data = load(args.file)
        set_path(data, parse_path(args.path), value)
        dump(args.file, data)
        print(f"set {args.path}")
        sys.exit(0)

    if args.cmd == "patch":
        if not (args.file and args.patch):
            ap.error("--file and --patch required")
        patch = json.load(open(args.patch))
        data = load(args.file)
        for path, value in patch.items():
            set_path(data, parse_path(path), value)
        dump(args.file, data)
        print(f"patched {len(patch)} path(s)")
        sys.exit(0)

    if args.cmd == "paths":
        if not args.file:
            ap.error("--file required")
        for p in leaf_paths(load(args.file)):
            print(p)
        sys.exit(0)

if __name__ == "__main__":
    main()
