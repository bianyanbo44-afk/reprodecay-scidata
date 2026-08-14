#!/usr/bin/env python3
"""Make a Pandoc-generated manuscript DOCX renderable and compact in Word."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.shared import Inches


def add_png_content_type(path: Path) -> None:
    """Add the package-wide PNG declaration expected by strict OOXML validators."""
    temporary = path.with_name(path.name + ".content-type.tmp")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "[Content_Types].xml" and b'Extension="png"' not in payload:
                marker = b"</Types>"
                declaration = b'<Default Extension="png" ContentType="image/png"/>'
                payload = payload.replace(marker, declaration + marker, 1)
            target.writestr(item, payload)
    temporary.replace(path)


def prepare(source: Path, destination: Path, width_inches: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    intermediate = destination.with_name(destination.name + ".python-docx.tmp")
    intermediate.unlink(missing_ok=True)
    document = Document(source)
    for shape in document.inline_shapes:
        if shape.width.inches > width_inches:
            ratio = shape.height / shape.width
            shape.width = Inches(width_inches)
            shape.height = int(shape.width * ratio)
    document.save(intermediate)
    shutil.copyfile(intermediate, destination)
    intermediate.unlink(missing_ok=True)
    add_png_content_type(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--width-inches", type=float, default=4.4)
    args = parser.parse_args()
    if args.width_inches <= 0:
        parser.error("--width-inches must be positive")
    prepare(args.source, args.destination, args.width_inches)
    print(f"prepared {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
