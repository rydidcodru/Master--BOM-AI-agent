import re
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree


SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def normalize_space(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def slide_number(path: str) -> int:
    match = SLIDE_RE.search(path)
    return int(match.group(1)) if match else 0


def xml_text(raw_xml: bytes) -> str:
    root = ElementTree.fromstring(raw_xml)
    fragments = []
    for element in root.iter():
        if element.tag.endswith("}t") and element.text:
            fragments.append(element.text)
        elif element.tag.endswith("}br"):
            fragments.append("\n")
    return normalize_space(" ".join(fragments))


def extract_slide_texts(pptx_bytes: bytes) -> list[dict[str, Any]]:
    slides: list[dict[str, Any]] = []
    with zipfile.ZipFile(BytesIO(pptx_bytes)) as archive:
        slide_paths = sorted(
            (path for path in archive.namelist() if SLIDE_RE.search(path)),
            key=slide_number,
        )
        for path in slide_paths:
            try:
                text = xml_text(archive.read(path))
            except ElementTree.ParseError:
                text = ""
            slides.append(
                {
                    "slide_number": slide_number(path),
                    "path": path,
                    "text": text,
                }
            )
    return slides
