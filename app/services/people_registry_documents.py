from __future__ import annotations

import re
from typing import Any, Optional

DOCUMENT_DIGITS_PATTERN = re.compile(r"\D+")


def format_document_id_for_display(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None

    digits = DOCUMENT_DIGITS_PATTERN.sub("", text)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return text
