"""Dependency-free repository skill/package checks; no agent installation."""
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
for provider in [".agents", ".claude"]:
    skill = root / provider / "skills/zudo-dashboard-setup/SKILL.md"
    text = skill.read_text()
    assert text.startswith("---\n"), f"Missing frontmatter: {skill}"
    front, body = text[4:].split("\n---\n", 1)
    fields = dict(line.split(": ", 1) for line in front.splitlines())
    assert fields["name"] == skill.parent.name
    assert re.fullmatch(r"[a-z0-9-]{1,63}", fields["name"])
    assert fields.get("description")
    assert len(body.splitlines()) < 50, "Keep skill logic in tested scripts"
    for link in re.findall(r"\]\(([^)]+)\)", body):
        assert (skill.parent / link).resolve().is_file(), f"Broken helper link: {link}"
    assert "TODO" not in text and "{{" not in text
print("Both agent skills and their shared helper links are valid.")
