"""SSML utilities for Microsoft TTS integration."""

from __future__ import annotations

import re
from html import unescape
import xml.etree.ElementTree as ET

RAW_SSML_SPEAK_CLOSE_RE = re.compile(r"</\s*speak\s*>", re.IGNORECASE)
RAW_SSML_SPEAK_NAMESPACE = "http://www.w3.org/2001/10/synthesis"
RAW_UNESCAPED_AMP_RE = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)"
)
XML_INVALID_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def local_name(tag: str) -> str:
    """Return local XML tag name without namespace."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def tag_with_ns(namespace: str | None, local: str) -> str:
    """Build namespaced XML tag name."""
    if namespace:
        return f"{{{namespace}}}{local}"
    return local


def apply_default_prosody_to_raw_ssml(
    ssml: str, prosody_options: dict[str, str]
) -> str:
    """Apply default prosody to raw SSML voices when no inline prosody exists."""
    try:
        root = ET.fromstring(ssml)
    except ET.ParseError:
        return ssml

    root_namespace: str | None = None
    if root.tag.startswith("{") and "}" in root.tag:
        root_namespace = root.tag[1:].split("}", 1)[0]

    prosody_tag = tag_with_ns(root_namespace, "prosody")
    changed = False

    for voice in root.iter():
        if local_name(voice.tag) != "voice":
            continue

        children = list(voice)
        has_inline_prosody = any(
            local_name(child.tag) == "prosody" for child in children
        )
        if has_inline_prosody or children:
            continue

        voice_text = voice.text or ""
        if not voice_text.strip():
            continue

        prosody = ET.Element(
            prosody_tag,
            {
                "rate": str(prosody_options["rate"]),
                "pitch": str(prosody_options["pitch"]),
                "volume": str(prosody_options["volume"]),
            },
        )
        prosody.text = voice_text
        voice.text = None
        voice.append(prosody)
        changed = True

    if not changed:
        return ssml

    return ET.tostring(root, encoding="unicode")


def ssml_to_plain_text(ssml: str) -> str:
    """Best-effort conversion from SSML/XML to readable plain text."""
    text = ssml
    text = re.sub(r"(?is)<\?.*?\?>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_raw_ssml_light(ssml: str) -> str:
    """Apply minimal SSML-safe repairs without changing valid formatting."""
    sanitized = XML_INVALID_CONTROL_CHARS_RE.sub(" ", ssml)
    sanitized = RAW_UNESCAPED_AMP_RE.sub("&amp;", sanitized)
    return sanitized


def find_xml_tag_end(text: str, start_idx: int, max_idx: int) -> int | None:
    """Find tag end ('>') handling quoted attributes."""
    in_single_quote = False
    in_double_quote = False
    idx = start_idx
    while idx < max_idx:
        char = text[idx]
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif char == ">" and not in_single_quote and not in_double_quote:
            return idx
        idx += 1
    return None


def extract_complete_top_level_ssml_units(
    inner_ssml: str,
) -> tuple[list[str], str, bool]:
    """Extract complete top-level units from SSML content inside <speak>."""
    units: list[str] = []
    depth = 0
    idx = 0
    unit_start: int | None = None
    last_consumed = 0

    close_match = RAW_SSML_SPEAK_CLOSE_RE.search(inner_ssml)
    saw_speak_close = close_match is not None
    parse_limit = close_match.start() if close_match else len(inner_ssml)

    while idx < parse_limit:
        if depth == 0:
            next_tag = inner_ssml.find("<", idx, parse_limit)
            if next_tag == -1:
                break
            if next_tag > idx:
                text_node = inner_ssml[idx:next_tag]
                if text_node.strip():
                    units.append(text_node.strip())
                last_consumed = next_tag
            idx = next_tag

        if idx >= parse_limit or inner_ssml[idx] != "<":
            break

        tag_end = find_xml_tag_end(inner_ssml, idx, parse_limit)
        if tag_end is None:
            break

        tag_token = inner_ssml[idx : tag_end + 1]
        stripped_tag = tag_token.strip()

        if stripped_tag.startswith("<?") or stripped_tag.startswith("<!"):
            idx = tag_end + 1
            last_consumed = idx
            continue

        is_closing_tag = stripped_tag.startswith("</")
        is_self_closing = stripped_tag.endswith("/>")

        if is_closing_tag:
            if depth > 0:
                depth -= 1
            if depth == 0 and unit_start is not None:
                units.append(inner_ssml[unit_start : tag_end + 1].strip())
                unit_start = None
                last_consumed = tag_end + 1
        else:
            if depth == 0:
                unit_start = idx
            if is_self_closing:
                if depth == 0 and unit_start is not None:
                    units.append(inner_ssml[unit_start : tag_end + 1].strip())
                    unit_start = None
                    last_consumed = tag_end + 1
            else:
                depth += 1

        idx = tag_end + 1

    if saw_speak_close and close_match is not None:
        close_start = close_match.start()
        close_end = close_match.end()
        trailing_before_close = inner_ssml[last_consumed:close_start]
        if not trailing_before_close.strip():
            last_consumed = close_end

    remainder = inner_ssml[last_consumed:]
    return units, remainder, saw_speak_close


def wrap_raw_ssml_unit(unit: str, language: str) -> str:
    """Wrap a raw SSML unit in a valid root <speak> document."""
    return (
        f"<speak xmlns='{RAW_SSML_SPEAK_NAMESPACE}' version='1.0' "
        f"xml:lang='{language}'>{unit}</speak>"
    )
