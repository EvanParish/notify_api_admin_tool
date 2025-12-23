import re
from typing import List


_PLACEHOLDER_PATTERN = re.compile(r"\(\((.*?)\)\)")


def extract_placeholders(content: str) -> List[str]:
    found = _PLACEHOLDER_PATTERN.findall(content or "")
    # Preserve order while removing duplicates
    seen = set()
    ordered: List[str] = []
    for item in found:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def validate_recipient(template_type: str, value: str) -> bool:
    if template_type == "email":
        return "@" in value and "." in value
    return value.isdigit() and len(value) >= 10
