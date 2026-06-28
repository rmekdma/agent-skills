from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "update-from-source.py"
SPEC = importlib.util.spec_from_file_location("update_from_source", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
update_from_source = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = update_from_source
SPEC.loader.exec_module(update_from_source)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


class UpdateFromSourceTests(unittest.TestCase):
    def test_generate_output_writes_codex_bundle_without_command_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            output = source / "agent-skills"

            write(
                source / "plugin.json",
                """
                {
                  "name": "upstream-plugin-name",
                  "version": "2.3.4",
                  "description": "Production-grade engineering skills for AI coding agents.",
                  "author": {"name": "Addy Osmani"},
                  "homepage": "https://github.com/addyosmani/agent-skills",
                  "repository": "https://github.com/addyosmani/agent-skills",
                  "license": "MIT"
                }
                """,
            )
            write(
                source / "skills" / "example-skill" / "SKILL.md",
                """
                ---
                name: example-skill
                description: Example skill
                ---

                # Example

                See `references/ref.md`, `agents/code-reviewer.md`, [reviewer](agents/code-reviewer.md),
                `code-reviewer.md`, and code-reviewer.md.
                Link variants [frag](agents/code-reviewer.md#usage), [see [reviewer]](agents/code-reviewer.md),
                [title](agents/code-reviewer.md "Reviewer (primary)"),
                [paren title](agents/code-reviewer.md "Reviewer ) primary"),
                and [angle](<agents/code-reviewer.md#usage>).
                Literal markdown stays untouched: `[reviewer](agents/code-reviewer.md)`.
                External URL stays untouched: https://example.com/agents/code-reviewer.md.
                """,
            )
            write(source / "references" / "ref.md", "# Reference\n")
            write(
                source / "references" / "orchestration-patterns.md",
                """
                # Orchestration Patterns

                Claude Code runs slash commands with personas.
                See `agents/code-reviewer.md`.
                """,
            )
            write(
                source / "agents" / "code-reviewer.md",
                """
                ---
                name: code-reviewer
                description: Review persona for Claude Code
                ---

                # Reviewer

                See `references/ref.md` and `skills/example-skill/SKILL.md`. See [agents/README.md](README.md).
                """,
            )
            write(
                source / "commands" / "planning.toml",
                """
                description = "Break work into small tasks"
                prompt = "Read CLAUDE.md before acting."
                """,
            )
            write(
                source / ".claude" / "commands" / "plan.md",
                """
                ---
                description: Plan
                ---

                Read CLAUDE.md before acting.
                """,
            )

            snapshot = update_from_source.generate_output(source, output)

            self.assertEqual(snapshot.skill_names, ("example-skill",))
            self.assertEqual(snapshot.agent_names, ("code-reviewer",))
            self.assertFalse((output / "skills" / "plan").exists())
            self.assertFalse((output / ".codex-agent-skills-generated").exists())
            self.assertFalse((output / ".codex").exists())

            manifest = json.loads((output / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "agent-skills")
            self.assertEqual(manifest["version"], "2.3.4")
            self.assertEqual(manifest["skills"], "./skills")
            self.assertNotIn("commands", manifest)

            skill_text = (output / "skills" / "example-skill" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("`../../references/ref.md`", skill_text)
            self.assertIn("`code-reviewer`", skill_text)
            self.assertNotIn(", [reviewer](agents/code-reviewer.md),", skill_text)
            self.assertIn("`[reviewer](agents/code-reviewer.md)`", skill_text)
            self.assertIn("https://example.com/agents/code-reviewer.md", skill_text)

            reference_text = (output / "references" / "orchestration-patterns.md").read_text(encoding="utf-8")
            self.assertNotIn("Claude Code", reference_text)
            self.assertNotIn("slash commands", reference_text)
            self.assertNotIn("personas", reference_text)
            self.assertIn("`code-reviewer`", reference_text)

            agent_toml_path = output / "agents" / "code-reviewer.toml"
            agent_toml_text = agent_toml_path.read_text(encoding="utf-8")
            agent_toml = tomllib.loads(agent_toml_text)
            self.assertEqual(agent_toml["name"], "code-reviewer")
            self.assertNotIn("prompt", agent_toml)
            self.assertNotIn("README.md", agent_toml_text)
            self.assertIn("`../references/ref.md`", agent_toml["developer_instructions"])
            self.assertIn("`../skills/example-skill/SKILL.md`", agent_toml["developer_instructions"])

    def test_generate_output_preserves_unmanaged_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            output = source / "agent-skills"
            write(source / "skills" / "example" / "SKILL.md", "# Example\n")
            write(source / "references" / "ref.md", "# Ref\n")
            write(source / "agents" / "code-reviewer.md", "# Reviewer\n")
            write(output / "tests" / "keep.txt", "keep\n")
            write(output / "custom.txt", "user owned\n")
            write(output / "skills" / "stale" / "SKILL.md", "# Stale\n")

            update_from_source.generate_output(source, output)

            self.assertTrue((output / "tests" / "keep.txt").exists())
            self.assertEqual((output / "custom.txt").read_text(encoding="utf-8"), "user owned\n")
            self.assertFalse((output / "skills" / "stale").exists())

    def test_ensure_source_requires_expected_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            write(source / "skills" / "example" / "SKILL.md", "# Example\n")
            write(source / "references" / "ref.md", "# Ref\n")

            with self.assertRaisesRegex(SystemExit, "agents"):
                update_from_source.ensure_source(source)

    def test_output_may_be_separate_repo_but_not_source_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            output = root / "output-repo"

            update_from_source.ensure_output_is_safe(source, output)
            with self.assertRaisesRegex(SystemExit, "상위"):
                update_from_source.ensure_output_is_safe(source, root)

    def test_remove_agent_readme_sentence_tolerates_spacing_and_punctuation(self) -> None:
        cases = {
            "Intro. See   [agents/README.md](README.md).": "Intro.",
            "Intro. See [docs/agents.md](../docs/agents.md).": "Intro.",
            "See\n[agents/README.md](README.md).": "",
            "See [agents/README.md](README.md), and continue.": "and continue.",
            "See [docs/agents.md](../docs/agents.md), and continue.": "and continue.",
            "Prefix See [agents/README.md](README.md); suffix.": "Prefix suffix.",
            "See [agents/README.md](README.md): details.": "details.",
            "Foresee [agents/README.md](README.md).": "Foresee [agents/README.md](README.md).",
        }

        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(update_from_source.remove_agent_readme_sentence(original), expected)

    def test_validation_rejects_generated_agent_toml_with_prompt_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "agent-skills"
            write(
                output / ".codex-plugin" / "plugin.json",
                """
                {
                  "name": "agent-skills",
                  "skills": "./skills"
                }
                """,
            )
            write(
                output / "agents" / "bad.toml",
                """
                name = "bad"
                description = "Bad"
                developer_instructions = "Do work"
                prompt = "Unsupported"
                """,
            )

            with self.assertRaisesRegex(ValueError, "unsupported field 'prompt'"):
                update_from_source.validate_output(output)

    def test_collect_relative_markdown_links_handles_markdown_and_code_spans(self) -> None:
        links = update_from_source.collect_relative_markdown_links(
            """
            See [agent](agents/code-reviewer.md), `README.md`,
            `../references/testing-patterns.md`, and `skills/{skill-name}/SKILL.md`.
            Ignore https://example.com/file.md and `docs/[topic].md`.
            """
        )

        self.assertEqual(
            links,
            {"agents/code-reviewer.md", "README.md", "../references/testing-patterns.md"},
        )

    def test_markdown_link_targets_normalize_fragments_queries_titles_and_escapes(self) -> None:
        links = update_from_source.collect_relative_markdown_links(
            """
            [fragment](missing.md#section)
            [query](other.md?raw=1)
            [title](third.md "Title")
            [angle](<fourth.md#heading>)
            [escaped](missing\\ file.md)
            [escaped fragment](other\\#file.md)
            [absolute](/tmp/missing.md)
            """
        )

        self.assertEqual(
            links,
            {"missing.md", "other.md", "third.md", "fourth.md", "missing file.md", "other#file.md"},
        )


if __name__ == "__main__":
    unittest.main()
