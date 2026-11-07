"""Count application Python source without tests, GUI markup or docstrings."""

import ast
import json
from pathlib import Path


def count(path):
    """Report physical source and a conservative non-comment code-line count."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    excluded = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                excluded.update(range(body[0].lineno, body[0].end_lineno + 1))
    lines = text.splitlines()
    return {
        "file": path.name,
        "physical": len(lines),
        "code": sum(
            bool(line.strip()) and not line.lstrip().startswith("#") and number not in excluded
            for number, line in enumerate(lines, 1)
        ),
    }


def main():
    """The counted set is only src/campus_market/*.py, excluding auxiliary tools."""
    root = Path(__file__).resolve().parents[1]
    files = [count(path) for path in sorted((root / "src/campus_market").glob("*.py"))]
    print(
        json.dumps(
            {
                "scope": "仅应用Python；排除测试、工具、HTML、CSS、JS、SQL和构建产物",
                "physical": sum(row["physical"] for row in files),
                "code": sum(row["code"] for row in files),
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
