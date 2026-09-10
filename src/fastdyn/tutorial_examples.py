"""Read the tutorial examples used by both mdBook and executable CI checks."""
from dataclasses import dataclass
from html import escape
from pathlib import Path
import re
import tomllib


MARKER = re.compile(r"^<!-- fastdyn-check: ([a-z0-9-]+) -->$", re.M)
BLOCK = re.compile(
    r"^<!-- fastdyn-check: (?P<id>[a-z0-9-]+) -->\n\s*"
    r"(?P<fence>`{3,})(?P<language>\w+)\n(?P<code>.*?)\n(?P=fence)[ \t]*(?=\n|$)",
    re.M | re.S,
)
INCLUDE = re.compile(r"\{\{#include ([^}\n]+)\}\}")


@dataclass(frozen=True)
class Example:
    id: str
    source: Path
    line: int
    language: str
    code: str


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Tutorial path escapes the checkout: {relative}")
    return path


def examples(root):
    found = {}
    for source in sorted((root / "docs/book").rglob("*.md")):
        if "vendor" in source.parts:
            continue
        text = source.read_text()
        matches = list(BLOCK.finditer(text))
        if [m[1] for m in MARKER.finditer(text)] != [m["id"] for m in matches]:
            raise ValueError(f"A fastdyn-check marker needs an immediately following code block: {source}")
        for match in matches:
            identifier = match["id"]
            if identifier in found:
                raise ValueError(f"Duplicate tutorial example: {identifier}")
            code = match["code"]
            if include := INCLUDE.fullmatch(code.strip()):
                target = inside(root, source.parent / include[1])
                code = target.read_text().rstrip("\n")
            elif "{{#include" in code:
                raise ValueError(f"{identifier}: checked examples support only whole-file includes")
            found[identifier] = Example(identifier, source.relative_to(root),
                text.count("\n", 0, match.start()) + 1, match["language"], code + "\n")
    return found


def suite(root, config=None):
    config = config or root / "tests/integration/tutorial.toml"
    settings = tomllib.loads(config.read_text())
    found = examples(root)
    steps = settings["steps"]
    ids = [step["id"] for step in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate tutorial step in the test manifest")
    if set(ids) != set(found):
        raise ValueError(f"Tutorial coverage mismatch: missing blocks={set(ids) - set(found)}, "
                         f"untested markers={set(found) - set(ids)}")
    for step in steps:
        example = found[step["id"]]
        if "write" in step:
            inside(root, step["write"])
        elif example.language != "bash":
            raise ValueError(f"{example.id}: {example.language} needs a file destination")
        for path in step.get("artifacts", []):
            inside(root, path)
    return settings, found


def decorate(book, context):
    root = Path(context["root"]).parent
    settings, _ = suite(root)
    steps = {step["id"]: step for step in settings["steps"]}
    repository = context["config"]["output"]["html"]["git-repository-url"].rstrip("/")
    url = escape(repository + "/actions/workflows/dev-container.yml", quote=True)

    def replace(match):
        step = steps[match["id"]]
        label = "CI-checked file" if "write" in step else "CI-checked command"
        detail = f"Save as <code>{escape(step['write'])}</code> · " if "write" in step else ""
        fence = match["fence"]
        return (f'<div class="fd-verified" data-example="{match["id"]}">\n'
                f'<p class="fd-verified-label"><a href="{url}">{label}</a>'
                f' <span>{detail}Nix-built Docker environment</span></p>\n\n'
                f'{fence}{match["language"]}\n{match["code"]}\n{fence}\n\n</div>')

    def visit(items):
        for item in items:
            if chapter := item.get("Chapter"):
                chapter["content"] = BLOCK.sub(replace, chapter["content"])
                visit(chapter.get("sub_items", []))
    visit(book.get("items", book.get("sections", [])))
