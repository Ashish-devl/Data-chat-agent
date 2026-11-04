"""Write docs/API.md from the server's OpenAPI description, so the reference never drifts.

python scripts/api_reference.py          # write docs/API.md
python scripts/api_reference.py --check  # fail if docs/API.md is out of date (CI)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "API.md"

SCOPES = {
    "health": "none",
    "ask": "ask",
    "feedback": "ask",
    "documents": "docs",
    "keys": "admin",
    "usage": "admin",
    "connections": "admin",
    "semantic layer": "admin",
    "console": "see notes",
}
ORDER = [
    "ask",
    "feedback",
    "documents",
    "connections",
    "semantic layer",
    "keys",
    "usage",
    "console",
    "health",
]


def _type(schema: dict, spec: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "anyOf" in schema:
        parts = [_type(s, spec) for s in schema["anyOf"]]
        return " or ".join(p for p in parts if p != "null") + (
            " (optional)" if "null" in parts else ""
        )
    if schema.get("type") == "array":
        return f"list of {_type(schema.get('items', {}), spec)}"
    if "enum" in schema:
        return " | ".join(f"`{v}`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    return schema.get("format") or schema.get("type") or "object"


def _fields(name: str, spec: dict) -> list[str]:
    schema = spec["components"]["schemas"][name]
    required = set(schema.get("required", []))
    rows = ["| Field | Type | Required | Notes |", "| --- | --- | --- | --- |"]
    for field, s in schema.get("properties", {}).items():
        default = (
            f" Default `{s['default']}`." if "default" in s and s["default"] not in ([], {}) else ""
        )
        notes = (s.get("description", "") + default).replace("\n", " ").replace("|", "\\|")
        rows.append(
            f"| `{field}` | {_type(s, spec)} | {'yes' if field in required else ''} | {notes} |"
        )
    return rows


def render() -> str:
    from datachat_agent.server.app import create_app

    spec = create_app().openapi()
    by_tag: dict[str, list] = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((path, method, op))

    lines = [
        "# API reference",
        "",
        "Generated from the server's OpenAPI description by `scripts/api_reference.py`; a running",
        "server also serves it interactively at `/docs`. Every path starts with `/v1` and every",
        "request except `/v1/health` and `/v1/auth/login` sends `Authorization: Bearer <key or",
        'console session token>`. Errors come back as `{"detail": "..."}` with a matching',
        "status: 401 (no or bad key), 403 (missing scope), 404, 409, 422 (invalid input),",
        "429 (rate or token limit; see `Retry-After`), 503 (the model is unavailable).",
        "",
        "Scopes: `ask` (questions, feedback), `docs` (PDFs), `admin` (everything). Publishable",
        "keys (`dc_pub_`) are ask-only and work only from their allowed websites. Console owners",
        "act as `admin`, members as `ask` + `docs`.",
        "",
        "| Group | Scope | Endpoints |",
        "| --- | --- | --- |",
    ]
    tags = sorted(by_tag, key=lambda t: ORDER.index(t) if t in ORDER else 99)
    for tag in tags:
        eps = ", ".join(f"`{m.upper()} {p}`" for p, m, _ in by_tag[tag])
        lines.append(f"| {tag.capitalize()} | {SCOPES.get(tag, '')} | {eps} |")

    seen_models: list[str] = []
    for tag in tags:
        lines += ["", f"## {tag.capitalize()}"]
        for path, method, op in by_tag[tag]:
            lines += ["", f"### `{method.upper()} {path}`", ""]
            summary = (op.get("description") or op.get("summary") or "").strip()
            if summary:
                lines += [summary.replace("\n", " "), ""]
            params = [p for p in op.get("parameters", []) if p["in"] in ("query", "path")]
            if params:
                lines += ["| Parameter | In | Type | Notes |", "| --- | --- | --- | --- |"]
                for p in params:
                    note = (p.get("description") or "").replace("\n", " ")
                    lines.append(
                        f"| `{p['name']}` | {p['in']} | {_type(p.get('schema', {}), spec)} "
                        f"| {note} |"
                    )
                lines.append("")
            body = op.get("requestBody", {}).get("content", {})
            for media, content in body.items():
                ref = content.get("schema", {}).get("$ref")
                if ref:
                    name = ref.rsplit("/", 1)[1]
                    lines.append(f"Body ({media}): [`{name}`](#{name.lower()})")
                    seen_models.append(name)
                else:
                    lines.append(f"Body: {media}")
            ok = next(
                (c for c in ("200", "201", "202", "204") if c in op.get("responses", {})), None
            )
            if ok:
                schema = (
                    op["responses"][ok]
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                shown = _type(schema, spec) if schema else "no body"
                for ref in [schema.get("$ref"), schema.get("items", {}).get("$ref")]:
                    if ref:
                        seen_models.append(ref.rsplit("/", 1)[1])
                        shown = f"[{shown}](#{ref.rsplit('/', 1)[1].lower()})"
                lines.append(f"Returns {ok}: {shown}")

    lines += ["", "## Models"]
    for name in dict.fromkeys(seen_models):
        lines += ["", f"### {name}", "", *_fields(name, spec)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "packages"))
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit("docs/API.md is out of date: run python scripts/api_reference.py")
        print("docs/API.md is up to date")
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT.relative_to(ROOT)}")
