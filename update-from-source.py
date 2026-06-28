#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple


BUNDLE_NAME = "agent-skills"
COPIED_DIRECTORIES = ("skills", "references")
REQUIRED_SOURCE_DIRECTORIES = (*COPIED_DIRECTORIES, "agents")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
FORBIDDEN_TERM_PATTERNS = (
    re.compile(r"\bClaude Code\b"),
    re.compile(r"\bCLAUDE\.md\b"),
    re.compile(r"\bCLAUDE_CODE\b"),
    re.compile(r"~/\.claude"),
    re.compile(r"\.claude/agents"),
    re.compile(r"\b[Ss]lash commands?\b"),
    re.compile(r"\bAgent tool\b"),
    re.compile(r"\bsubagent_type\b"),
    re.compile(r"\b[Pp]ersonas?\b"),
    re.compile(r"/mnt/skills/user/"),
)


class MarkdownLinkSpan(NamedTuple):
    start: int
    end: int
    raw_target: str


@dataclass(frozen=True)
class AgentEntry:
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class SourceSnapshot:
    source: Path
    plugin_metadata: dict[str, Any]
    skill_names: tuple[str, ...]
    agent_entries: tuple[AgentEntry, ...]
    commit: str

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.agent_entries)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="현재 agent-skills source clone을 Codex용 output repo로 변환합니다.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=script_dir.parent,
        help="원본 agent-skills clone 경로입니다. 기본값은 이 스크립트의 parent directory입니다.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=script_dir,
        help="변환 결과를 쓸 output repo 경로입니다. 기본값은 이 스크립트의 directory입니다.",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def ensure_source(source: Path) -> None:
    missing = [directory for directory in REQUIRED_SOURCE_DIRECTORIES if not (source / directory).is_dir()]
    if missing:
        fail(f"원본 저장소에 필요한 디렉터리가 없습니다: {', '.join(missing)}")


def ensure_output_is_safe(source: Path, output: Path) -> None:
    if source == output:
        fail("source와 output은 같은 디렉터리일 수 없습니다")
    source_parts = source.resolve().parts
    output_parts = output.resolve().parts
    if len(output_parts) < len(source_parts) and source_parts[: len(output_parts)] == output_parts:
        fail("output은 source의 상위 디렉터리일 수 없습니다")


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_markdown_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = unquote_yaml_scalar(value)
    return fields, text[match.end() :]


def discover_skill_names(source: Path) -> tuple[str, ...]:
    names: list[str] = []
    for skill_md in sorted((source / "skills").glob("*/SKILL.md")):
        folder = skill_md.parent.name
        fields, _ = parse_markdown_frontmatter(skill_md)
        name = fields.get("name", folder)
        if name != folder:
            fail(f"SKILL.md name '{name}' does not match folder '{folder}'")
        names.append(folder)

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        fail(f"duplicate skill entries: {', '.join(duplicates)}")
    if not names:
        fail("skills/ 아래에서 SKILL.md를 찾지 못했습니다")
    return tuple(names)


def discover_agent_entries(source: Path) -> tuple[AgentEntry, ...]:
    entries: list[AgentEntry] = []
    for agent_path in sorted((source / "agents").glob("*.md")):
        if agent_path.name == "README.md":
            continue
        fields, body = parse_markdown_frontmatter(agent_path)
        name = fields.get("name", agent_path.stem)
        if name != agent_path.stem:
            fail(f"agent name '{name}' does not match file '{agent_path.name}'")
        entries.append(
            AgentEntry(
                name=name,
                description=fields.get("description", ""),
                body=body,
            )
        )

    duplicates = sorted({entry.name for entry in entries if [item.name for item in entries].count(entry.name) > 1})
    if duplicates:
        fail(f"duplicate agent entries: {', '.join(duplicates)}")
    return tuple(entries)


def read_plugin_metadata(source: Path) -> dict[str, Any]:
    path = source / "plugin.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail(f"{path} must contain a JSON object")
    return data


def source_commit(source: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def load_source_snapshot(source: Path) -> SourceSnapshot:
    return SourceSnapshot(
        source=source,
        plugin_metadata=read_plugin_metadata(source),
        skill_names=discover_skill_names(source),
        agent_entries=discover_agent_entries(source),
        commit=source_commit(source),
    )


def rewrite_terms(text: str) -> str:
    replacements = {
        "Claude Code settings": "Codex settings",
        "Claude Code Simplifier plugin": "code simplifier plugin",
        "Claude Code": "Codex",
        "Agent Teams teammates** (experimental, requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`)": (
            "multi-agent teammates** (when supported by the active Codex environment)"
        ),
        "Experimental — requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`": "Environment-dependent",
        "**CLAUDE.md** (for Claude Code):": "**AGENTS.md** (for Codex):",
        "**CLAUDE.md / rules files**": "**AGENTS.md / rules files**",
        "Rules files (CLAUDE.md etc.)": "Rules files (AGENTS.md etc.)",
        "Rules Files (CLAUDE.md, etc.)": "Rules Files (AGENTS.md, etc.)",
        "CLAUDE.md / project conventions": "AGENTS.md / project conventions",
        "CLAUDE.md or equivalent": "AGENTS.md or equivalent",
        "Read CLAUDE.md and study project conventions": "Read AGENTS.md and study project conventions",
        "CLAUDE.md": "AGENTS.md",
        "`README.md`": "the project README",
        "Slash commands": "Source commands",
        "Slash command": "Source command",
        "slash commands": "source commands",
        "slash command": "source command",
        "`subagent_type:": "`agent name:",
        "`subagent_type`": "`agent name`",
        "In Claude Code, each call passes `subagent_type` matching the persona's `name` field:": (
            "In Codex, start each subagent by its configured agent name:"
        ),
        "In Claude Code, each call passes `subagent_type` matching the subagent prompt's `name` field:": (
            "In Codex, start each subagent by its configured agent name:"
        ),
        "Agent tool": "Codex subagent tool",
        "Claude Code's subagent model": "Codex's subagent model",
        "Claude Code Agent Teams": "Codex multi-agent tools",
        "`.claude/agents/` or `~/.claude/agents/`": "`~/.codex/agents/`",
        "main agent (not a sub-persona)": "main agent (not a subagent)",
        "plugin subagents sit at the bottom of Claude Code's scope priority table": (
            "user-level Codex agents override generated bundle agents"
        ),
        "this plugin's versions": "the generated bundle versions",
        "this plugin's version": "the generated bundle version",
        "Do NOT add this skill to a persona's `skills:` frontmatter.** A persona that follows Step 3 would spawn another persona": (
            "Do NOT run this skill from inside a subagent prompt.** A subagent that follows Step 3 would spawn another subagent"
        ),
        '"personas do not invoke other personas"': '"subagents do not invoke other subagents"',
        "persona's default response shape.** Personas like": (
            "reviewer prompt's default response shape.** Reviewer prompts like"
        ),
        "If a persona's response shape": "If a reviewer prompt's response shape",
        "A persona calling another persona": "A subagent calling another subagent",
        "where Claude Code prevents nested subagent spawn": (
            "where Codex normally prevents nested subagent spawn"
        ),
        "In Claude Code, the role-based reviewers": "In Codex, the role-based reviewers",
    }
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(old, new)

    text = re.sub(
        r"Agent Teams is experimental\. In `~/\.claude/settings\.json`:\n\n"
        r"```json\n\{\n  \"env\": \{\n    \"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS\": \"1\"\n  \}\n\}\n```\n\n"
        r"Requires Codex v[^\n]+\.",
        "Multi-agent teammate support depends on the active Codex environment. Confirm the required setup in your Codex installation before using this pattern.",
        text,
    )
    text = text.replace("without an Codex subagent tool", "without a Codex subagent tool")

    word_replacements = (
        (r"\bPersonas\b", "Subagent prompts"),
        (r"\bpersonas\b", "subagent prompts"),
        (r"\bPersona\b", "Subagent prompt"),
        (r"\bpersona\b", "subagent prompt"),
    )
    for pattern, replacement in word_replacements:
        text = re.sub(pattern, replacement, text)

    return text


def rewrite_common_skill_links(text: str, agent_names: tuple[str, ...]) -> str:
    text = re.sub(
        r"`skills/([a-z0-9-]+)/SKILL\.md`\s+\(`\1`\)",
        r"`\1` (`../\1/SKILL.md`)",
        text,
    )
    text = re.sub(r"`skills/([a-z0-9-]+)/SKILL\.md`", r"`../\1/SKILL.md`", text)
    text = re.sub(r"`references/([A-Za-z0-9_.-]+\.md)`", r"`../../references/\1`", text)
    return replace_agent_file_paths_with_names(text, agent_names)


def rewrite_agent_toml_links(text: str, agent_names: tuple[str, ...]) -> str:
    text = re.sub(r"`skills/([a-z0-9-]+)/SKILL\.md`", r"`../skills/\1/SKILL.md`", text)
    text = re.sub(r"`references/([A-Za-z0-9_.-]+\.md)`", r"`../references/\1`", text)
    text = replace_agent_file_paths_with_names(text, agent_names)
    text = re.sub(r"\]\(../references/([A-Za-z0-9_.-]+\.md)\)", r"](../references/\1)", text)
    return text


def rewrite_reference_links(text: str, agent_names: tuple[str, ...]) -> str:
    text = re.sub(r"`skills/([a-z0-9-]+)/SKILL\.md`", r"`../skills/\1/SKILL.md`", text)
    text = re.sub(r"`references/([A-Za-z0-9_.-]+\.md)`", r"`\1`", text)
    return replace_agent_file_paths_with_names(text, agent_names)


def replace_agent_file_paths_with_names(text: str, agent_names: tuple[str, ...]) -> str:
    if not agent_names:
        return text

    agent_pattern = "|".join(re.escape(agent_name) for agent_name in sorted(agent_names, key=len, reverse=True))
    text = replace_agent_markdown_links(text, agent_pattern)
    text = re.sub(
        rf"`(?:(?:(?:\./|\.\./)*agents/)|\./)?({agent_pattern})\.md(?:(?:[?#][^`]*)|(?::[^`]*))?`",
        r"`\1`",
        text,
    )
    code_span_replacements: dict[str, str] = {}

    def protect_non_path_code_span(match: re.Match[str]) -> str:
        value = match.group(1)
        if markdown_path_from_code_span(value):
            return match.group(0)
        placeholder = f"@@CODEX_CODE_SPAN_{len(code_span_replacements)}@@"
        code_span_replacements[placeholder] = match.group(0)
        return placeholder

    text = re.sub(r"`([^`\n]+)`", protect_non_path_code_span, text)

    def replace_plain_agent_ref(match: re.Match[str]) -> str:
        punctuation = match.group(2) or ""
        return f"`{match.group(1)}`{punctuation}"

    text = re.sub(
        rf"(?<![A-Za-z0-9_./:?#%=&@-])(?:(?:\./|\.\./)*agents/|\./)({agent_pattern})\.md(?:[?#][^\s`)\]}}>,;:.!?]*)?([:.!?])?(?=$|\s|[`)\]}}>,;:])",
        replace_plain_agent_ref,
        text,
    )
    text = re.sub(
        rf"(?<![A-Za-z0-9_./:?#%=&@-])({agent_pattern})\.md(?:[?#][^\s`)\]}}>,;:.!?]*)?([:.!?])?(?=$|\s|[`)\]}}>,;:])",
        replace_plain_agent_ref,
        text,
    )
    for placeholder, original in code_span_replacements.items():
        text = text.replace(placeholder, original)
    return text


def agent_name_from_markdown_path(path: str, agent_pattern: str) -> str | None:
    match = re.fullmatch(rf"(?:(?:(?:\./|\.\./)*agents/)|\./)?({agent_pattern})\.md", path)
    return match.group(1) if match else None


def replace_agent_markdown_links(text: str, agent_pattern: str) -> str:
    result: list[str] = []
    index = 0
    for span in iter_markdown_link_spans(text):
        agent_name = agent_name_from_markdown_path(
            normalize_markdown_link_target(span.raw_target),
            agent_pattern,
        )
        if not agent_name:
            continue
        result.append(text[index : span.start])
        result.append(f"`{agent_name}`")
        index = span.end
    result.append(text[index:])
    return "".join(result)


def remove_agent_readme_sentence(text: str) -> str:
    pattern = re.compile(
        r"(?<![A-Za-z])\s*See\s+"
        r"(?:\[agents/README\.md\]\(README\.md\)|\[docs/agents\.md\]\(\.\./docs/agents\.md\))"
        r"(?:\s*[,;.:!?-]?\s*)?"
    )

    def remove_with_spacing(match: re.Match[str]) -> str:
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before and after and not before.isspace() and not after.isspace() and after not in ".,;:!?)]}":
            return " "
        return ""

    return pattern.sub(remove_with_spacing, text)


def rewrite_skill_text(text: str, skill_name: str, agent_names: tuple[str, ...]) -> str:
    text = rewrite_terms(text)
    text = rewrite_common_skill_links(text, agent_names)
    return text.replace(f"/mnt/skills/user/{skill_name}/", "")


def rewrite_reference_text(text: str, agent_names: tuple[str, ...]) -> str:
    text = rewrite_terms(text)
    return rewrite_reference_links(text, agent_names)


def rewrite_agent_toml_text(text: str, agent_names: tuple[str, ...]) -> str:
    text = remove_agent_readme_sentence(text)
    text = rewrite_terms(text)
    return rewrite_agent_toml_links(text, agent_names)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def refresh_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        remove_path(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_transformed_skills(source: Path, skills_root: Path, snapshot: SourceSnapshot) -> None:
    if skills_root.exists() or skills_root.is_symlink():
        remove_path(skills_root)
    shutil.copytree(source / "skills", skills_root)
    for path in sorted(skills_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if path.name == "SKILL.md":
            text = rewrite_skill_text(text, path.parent.name, snapshot.agent_names)
        else:
            text = rewrite_terms(text)
            text = replace_agent_file_paths_with_names(text, snapshot.agent_names)
        path.write_text(text, encoding="utf-8")


def copy_transformed_references(source: Path, references_root: Path, snapshot: SourceSnapshot) -> None:
    if references_root.exists() or references_root.is_symlink():
        remove_path(references_root)
    shutil.copytree(source / "references", references_root)
    for path in sorted(references_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        path.write_text(rewrite_reference_text(text, snapshot.agent_names), encoding="utf-8")


def toml_multiline(value: str) -> str:
    if "'''" not in value:
        return "'''\n" + value.rstrip() + "\n'''"
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return '"""\n' + escaped.rstrip() + '\n"""'


def toml_string(value: str) -> str:
    return json.dumps(value)


def write_agent_tomls(agents_root: Path, snapshot: SourceSnapshot) -> None:
    refresh_directory(agents_root)
    for entry in snapshot.agent_entries:
        description = rewrite_terms(entry.description)
        body = rewrite_agent_toml_text(entry.body, snapshot.agent_names)
        toml = (
            f"name = {toml_string(entry.name)}\n\n"
            f"description = {toml_multiline(description)}\n\n"
            f"developer_instructions = {toml_multiline(body)}\n"
        )
        (agents_root / f"{entry.name}.toml").write_text(toml, encoding="utf-8")


def metadata_string(metadata: dict[str, Any], key: str, default: str) -> str:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else default


def write_plugin_json(output: Path, snapshot: SourceSnapshot) -> None:
    metadata = snapshot.plugin_metadata
    payload: dict[str, Any] = {
        "name": BUNDLE_NAME,
        "version": metadata_string(metadata, "version", "1.0.0"),
        "description": rewrite_terms(
            metadata_string(
                metadata,
                "description",
                "Production-grade engineering skills for AI coding agents.",
            )
        ),
        "skills": "./skills",
    }
    for key in ("author", "homepage", "repository", "license", "keywords"):
        value = metadata.get(key)
        if value is not None:
            payload[key] = value

    plugin_dir = output / ".codex-plugin"
    refresh_directory(plugin_dir)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_readme(output: Path, snapshot: SourceSnapshot) -> None:
    if snapshot.commit == "unknown":
        commit_line = "- 사용한 source commit: `unknown`"
    else:
        commit_line = (
            "- 사용한 source commit: "
            f"[`{snapshot.commit[:7]}`](https://github.com/addyosmani/agent-skills/commit/{snapshot.commit})"
        )
    body = f"""# Agent Skills for Codex

이 repo는 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)를 Codex 용도로 변환한 결과물입니다.

{commit_line}

## 설치

```bash
install_root="$HOME/.agents/skills/agent-skills"
agents_root="$HOME/.codex/agents"
mkdir -p "$install_root" "$agents_root"

rm -rf "$install_root/references" "$install_root/skills" "$install_root/.codex-plugin"
cp -R references skills .codex-plugin "$install_root"/
cp agents/*.toml "$agents_root"/
```

## Update

원본 clone이 업데이트되면 다음 명령으로 이 Codex용 출력물을 다시 생성합니다.

```bash
git clone git@github.com:addyosmani/agent-skills.git
cd agent-skills
git clone git@github.com:rmekdma/agent-skills.git
./agent-skills/update-from-source.py
cd agent-skills
git add . && git commit -m "update" && git push origin main
```
"""
    (output / "README.md").write_text(body, encoding="utf-8")


def copy_script(script: Path, output: Path) -> None:
    destination = output / "update-from-source.py"
    if script.resolve() == destination.resolve():
        return
    shutil.copy2(script, destination)


def generate_output(source: Path, output: Path, script: Path | None = None) -> SourceSnapshot:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    ensure_source(source)
    ensure_output_is_safe(source, output)
    snapshot = load_source_snapshot(source)

    output.mkdir(parents=True, exist_ok=True)
    write_readme(output, snapshot)
    write_plugin_json(output, snapshot)
    copy_transformed_skills(source, output / "skills", snapshot)
    copy_transformed_references(source, output / "references", snapshot)
    write_agent_tomls(output / "agents", snapshot)
    copy_script(script or Path(__file__).resolve(), output)
    validate_output(output)
    return snapshot


def validate_output(output: Path) -> None:
    errors: list[str] = []
    if (output / ".codex-agent-skills-generated").exists():
        errors.append(f"{output / '.codex-agent-skills-generated'}: generated marker must not be written")
    if (output / ".codex").exists():
        errors.append(f"{output / '.codex'}: .codex directory must not be generated")

    errors.extend(validate_codex_plugin_manifest(output))

    checked_roots = [
        output / "skills",
        output / "references",
        output / "agents",
        output / ".codex-plugin",
    ]
    for root in checked_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".md", ".toml", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_TERM_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{path}: forbidden term pattern {pattern.pattern!r}")
            if path.suffix == ".toml":
                try:
                    toml_data = tomllib.loads(text)
                except tomllib.TOMLDecodeError as exc:
                    errors.append(f"{path}: invalid TOML: {exc}")
                else:
                    if path.parent == output / "agents":
                        errors.extend(validate_codex_agent_toml(path, toml_data))
            if path.suffix in {".md", ".toml"}:
                errors.extend(validate_relative_links(path, text))

    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"생성된 Codex 출력물 검증에 실패했습니다:\n{joined}")


def validate_codex_plugin_manifest(output: Path) -> list[str]:
    manifest_path = output / ".codex-plugin" / "plugin.json"
    if not manifest_path.exists():
        return [f"{manifest_path}: missing Codex plugin manifest"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{manifest_path}: invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"{manifest_path}: manifest must be a JSON object"]

    errors: list[str] = []
    if data.get("name") != BUNDLE_NAME:
        errors.append(f"{manifest_path}: name must be {BUNDLE_NAME!r}")
    if data.get("skills") != "./skills":
        errors.append(f"{manifest_path}: skills must be './skills'")
    if "commands" in data:
        errors.append(f"{manifest_path}: commands must not be generated")
    return errors


def validate_codex_agent_toml(path: Path, data: object) -> list[str]:
    if not isinstance(data, dict):
        return [f"{path}: agent TOML must decode to a table"]

    errors: list[str] = []
    for field in ("name", "description", "developer_instructions"):
        if not isinstance(data.get(field), str) or not data.get(field):
            errors.append(f"{path}: missing required string field {field!r}")
    if "prompt" in data:
        errors.append(f"{path}: unsupported field 'prompt'; use 'developer_instructions'")
    return errors


def validate_relative_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    candidates = collect_relative_markdown_links(text)
    for candidate in sorted(candidates):
        if not (path.parent / candidate).resolve().exists():
            errors.append(f"{path}: broken relative link {candidate}")
    return errors


def collect_relative_markdown_links(text: str) -> set[str]:
    candidates: set[str] = set()
    candidates.update(extract_markdown_link_targets(text))
    candidates.update(extract_backticked_markdown_paths(text))
    return {candidate for candidate in candidates if should_validate_markdown_path(candidate)}


def extract_markdown_link_targets(text: str) -> set[str]:
    links: set[str] = set()
    for raw_target in iter_markdown_link_targets(text):
        link = normalize_markdown_link_target(raw_target)
        if link.endswith(".md"):
            links.add(link)
    return links


def iter_markdown_link_targets(text: str) -> list[str]:
    return [span.raw_target for span in iter_markdown_link_spans(text)]


def iter_markdown_link_spans(text: str) -> list[MarkdownLinkSpan]:
    spans: list[MarkdownLinkSpan] = []
    code_spans = find_code_span_ranges(text)
    index = 0
    while True:
        close_bracket = text.find("](", index)
        if close_bracket == -1:
            return spans
        if in_any_range(close_bracket, code_spans):
            index = close_bracket + 2
            continue

        open_bracket = find_markdown_label_start(text, close_bracket, index)
        if open_bracket is None:
            index = close_bracket + 2
            continue
        link_start = open_bracket - 1 if open_bracket > 0 and text[open_bracket - 1] == "!" else open_bracket

        cursor = close_bracket + 2
        start = cursor
        depth = 0
        in_angle = False
        quote_char: str | None = None
        escaped = False
        while cursor < len(text):
            char = text[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote_char:
                if char == quote_char:
                    quote_char = None
            elif not in_angle and char in {'"', "'"}:
                quote_char = char
            elif char == "<" and cursor == start:
                in_angle = True
            elif char == ">" and in_angle:
                in_angle = False
            elif not in_angle and char == "(":
                depth += 1
            elif not in_angle and char == ")":
                if depth == 0:
                    spans.append(MarkdownLinkSpan(link_start, cursor + 1, text[start:cursor]))
                    index = cursor + 1
                    break
                depth -= 1
            cursor += 1
        else:
            return spans


def find_markdown_label_start(text: str, close_bracket: int, min_index: int) -> int | None:
    depth = 0
    cursor = close_bracket
    while cursor >= min_index:
        char = text[cursor]
        if char == "]":
            depth += 1
        elif char == "[":
            depth -= 1
            if depth == 0:
                return cursor
        cursor -= 1
    return None


def find_code_span_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    index = 0
    while True:
        start = text.find("`", index)
        if start == -1:
            return ranges
        end = text.find("`", start + 1)
        if end == -1:
            return ranges
        ranges.append((start, end + 1))
        index = end + 1


def in_any_range(index: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)


def normalize_markdown_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if not target:
        return ""
    if target.startswith("<"):
        end = find_unescaped_angle_close(target)
        target = target[1:end] if end != -1 else target[1:]
    else:
        target = first_markdown_target_token(target)
    target = strip_unescaped_fragment_or_query(target)
    return unescape_markdown_target(target)


def find_unescaped_angle_close(target: str) -> int:
    escaped = False
    for index, char in enumerate(target[1:], start=1):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ">":
            return index
    return -1


def strip_unescaped_fragment_or_query(target: str) -> str:
    escaped = False
    for index, char in enumerate(target):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"?", "#"}:
            return target[:index]
    return target


def first_markdown_target_token(target: str) -> str:
    escaped = False
    for index, char in enumerate(target):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char.isspace():
            return target[:index]
    return target


def unescape_markdown_target(target: str) -> str:
    result: list[str] = []
    escaped = False
    for char in target:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)
    if escaped:
        result.append("\\")
    return "".join(result)


def extract_backticked_markdown_paths(text: str) -> set[str]:
    candidates: set[str] = set()
    for raw_value in re.findall(r"`([^`\n]+\.md(?:[^\n`]*)?)`", text):
        value = markdown_path_from_code_span(raw_value)
        if not value:
            continue
        if value == "README.md" or value.startswith(("./", "../")) or "/" in value or value != raw_value.strip():
            candidates.add(value)
    return candidates


def markdown_path_from_code_span(raw_value: str) -> str | None:
    value = raw_value.strip()
    if not value:
        return None
    token = value.split(maxsplit=1)[0]
    if not re.match(r"^(?:\.{0,2}/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.md(?:[?#:].*)?$", token):
        return None
    path = normalize_markdown_link_target(token)
    if ":" in path:
        path = path.split(":", 1)[0]
    return path if path.endswith(".md") else None


def should_validate_markdown_path(link: str) -> bool:
    if not link or re.match(r"^[a-z]+://", link) or link.startswith("#"):
        return False
    if Path(link).is_absolute():
        return False
    if re.search(r"[*\[\]]", link):
        return False
    parts = [part for part in Path(link).parts if part not in {".", ".."}]
    if any(part.startswith(".") for part in parts):
        return False
    return not any(
        re.fullmatch(r"(?:\{[A-Za-z0-9_-]+\}|<[A-Za-z0-9_-]+>)(?:\.md)?", part)
        for part in parts
    )


def main() -> None:
    args = parse_args()
    snapshot = generate_output(args.source, args.output)
    print(f"Synced {len(snapshot.skill_names)} skills into {args.output.expanduser().resolve()}")
    print(f"Wrote {len(snapshot.agent_entries)} agent TOML files")


if __name__ == "__main__":
    main()
