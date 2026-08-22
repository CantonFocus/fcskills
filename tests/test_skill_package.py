from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "fcs-rename"


class SkillPackageTests(unittest.TestCase):
    def test_registered_skill_name_and_brand_are_preserved(self):
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\n"))
        frontmatter = content.split("---", 2)[1]
        self.assertIn("\nname: fcs-rename\n", "\n" + frontmatter)
        self.assertIn("试试就知道了", frontmatter)

    def test_installable_skill_contains_all_runtime_files(self):
        expected = {
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "agents" / "openai.yaml",
            SKILL_ROOT / "scripts" / "runtime_support.py",
            SKILL_ROOT / "scripts" / "prepare_local_asr.py",
            SKILL_ROOT / "scripts" / "analyze_media_assets.py",
            SKILL_ROOT / "scripts" / "rename_media_assets.py",
        }
        self.assertEqual([str(path) for path in expected if not path.is_file()], [])

    def test_readme_keeps_public_global_install_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "npx -y skills add CantonFocus/fcskills -g --all",
            readme,
        )

    def test_public_files_do_not_contain_known_private_identifiers(self):
        private_markers = (
            "/Users/" + "share",
            "sharehoho" + "10",
            "福克" + " ok",
        )
        roots = [
            ROOT / "README.md",
            ROOT / "THIRD_PARTY_NOTICES.md",
            ROOT / "docs",
            ROOT / "skills",
        ]
        offenders: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else list(root.rglob("*"))
            for path in paths:
                if not path.is_file() or path.name == ".DS_Store":
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for marker in private_markers:
                    if marker in content:
                        offenders.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
