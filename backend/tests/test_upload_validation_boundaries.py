import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.uploads import validate_image_bytes


class ImageValidationBoundaryTests(unittest.TestCase):
    def test_unidentified_image_is_rejected_as_415(self):
        with patch("PIL.Image.open", side_effect=UnidentifiedImageError("bad image")):
            with self.assertRaises(HTTPException) as caught:
                validate_image_bytes(b"not-an-image")
        self.assertEqual(caught.exception.status_code, 415)

    def test_decompression_bomb_error_is_rejected_as_415(self):
        with patch(
            "PIL.Image.open",
            side_effect=Image.DecompressionBombError("too many pixels"),
        ):
            with self.assertRaises(HTTPException) as caught:
                validate_image_bytes(b"compressed-image")
        self.assertEqual(caught.exception.status_code, 415)

    def test_unexpected_runtime_error_is_not_mislabeled_as_bad_upload(self):
        with patch("PIL.Image.open", side_effect=RuntimeError("programming bug")):
            with self.assertRaisesRegex(RuntimeError, "programming bug"):
                validate_image_bytes(b"image")


class ImageValidationSourceGuardTests(unittest.TestCase):
    def test_validate_image_bytes_has_no_broad_exception_handler(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "uploads.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name != "validate_image_bytes":
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append(getattr(child, "lineno", -1))
                if isinstance(child.type, ast.Tuple):
                    if any(
                        isinstance(item, ast.Name) and item.id == "Exception"
                        for item in child.type.elts
                    ):
                        offenders.append(getattr(child, "lineno", -1))
        self.assertEqual(offenders, [], f"broad image validation handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
