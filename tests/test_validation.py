"""Repository packaging failures should be caught before distribution."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_skill.py"


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(VALIDATOR.is_file(), "Package validator is missing")
        spec = importlib.util.spec_from_file_location("validate_skill", VALIDATOR)
        self.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.validator)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "dead-end-detector"
        self.folder.mkdir()

    def writeSkill(self, front="name: dead-end-detector\ndescription: Use when work stalls.", body="# Detector\n"):
        (self.folder / "SKILL.md").write_text(f"---\n{front}\n---\n{body}")

    def test_actual_package_validates(self):
        result = self.validator.validate(ROOT / "skills" / "dead-end-detector")
        self.assertLessEqual(result["words"], 500)

    def test_invalid_name_and_folder_mismatch_are_rejected(self):
        for name in ("Bad_Name", "double--hyphen", "different-folder", "x" * 65):
            with self.subTest(name=name):
                self.writeSkill(front=f"name: {name}\ndescription: Use when work stalls.")
                with self.assertRaises(ValueError):
                    self.validator.validate(self.folder)

    def test_missing_or_duplicate_description_is_rejected(self):
        for front in ("name: dead-end-detector", "name: dead-end-detector\ndescription: Use when work stalls.\ndescription: another"):
            self.writeSkill(front=front)
            with self.assertRaises(ValueError):
                self.validator.validate(self.folder)

    def test_broken_reference_is_rejected(self):
        self.writeSkill(body="[Contract](references/missing.md)\n")
        with self.assertRaises(ValueError):
            self.validator.validate(self.folder)

    def test_external_local_reference_is_rejected(self):
        outside = self.folder.parent / "outside.md"
        outside.write_text("context\n")
        self.writeSkill(body="[Context](../outside.md)\n")
        with self.assertRaises(ValueError):
            self.validator.validate(self.folder)

    def test_instruction_only_package_validates(self):
        self.writeSkill()
        try:
            result = self.validator.validate(self.folder)
        except ValueError as error:
            self.fail(f"Instruction-only package was rejected: {error}")
        self.assertEqual(result["name"], "dead-end-detector")
        self.assertEqual(list(self.folder.iterdir()), [self.folder / "SKILL.md"])


if __name__ == "__main__":
    unittest.main()
