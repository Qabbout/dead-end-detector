"""Checks for the portable package, without host-specific dependencies."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "dead-end-detector"


class SkillTests(unittest.TestCase):
    def test_standard_entrypoint_exists(self):
        self.assertTrue((SKILL / "SKILL.md").is_file(), "Portable skill is missing")

    def test_entrypoint_has_bounded_size_and_precise_triggers(self):
        path = SKILL / "SKILL.md"
        self.assertTrue(path.is_file(), "Portable skill is missing")
        text = path.read_text()
        self.assertLessEqual(len(text.split()), 500)
        self.assertIn("name: dead-end-detector", text)
        self.assertIn("description: Use when", text)
        self.assertIn("hypothesis", text)
        self.assertIn("permission", text)
        self.assertIn("waiting", text)

    def test_installable_package_contains_only_entrypoint_and_license(self):
        files = {path.relative_to(SKILL).as_posix()
                 for path in SKILL.rglob("*") if path.is_file()}
        self.assertEqual(files, {"SKILL.md", "LICENSE"})
        self.assertEqual((SKILL / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())

    def test_core_avoids_host_specific_frontmatter(self):
        path = SKILL / "SKILL.md"
        self.assertTrue(path.is_file(), "Portable skill is missing")
        frontmatter = path.read_text().split("---", 2)[1]
        self.assertNotIn("hooks:", frontmatter)
        self.assertNotIn("allowed-tools:", frontmatter)
        self.assertNotIn("disable-model-invocation:", frontmatter)


if __name__ == "__main__":
    unittest.main()
