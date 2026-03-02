from __future__ import annotations

import re
from typing import List, Tuple


_LOC_RE = re.compile(r'\(line\s+(?P<ln>\d+)\s+of\s+"(?P<path>[^"]+)"\)')


def _parse_location(seg: str) -> Tuple[int, str] | None:
    m = _LOC_RE.search(seg)
    if not m:
        return None
    return int(m.group("ln")), m.group("path").replace("\\", "/")


def _strip_location_paths(text: str) -> str:
    return _LOC_RE.sub(lambda m: f'(line {m.group("ln")})', text)


def _format_block(idx: int, block: str) -> str:
    clean_block = _strip_location_paths(block).strip()
    lines = clean_block.splitlines()
    cleaned_lines = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        cleaned_lines.append(cleaned)
        if cleaned.startswith("At command"):
            break
    clean_block = "\n".join(cleaned_lines)
    if not clean_block:
        return ""
    numbers = [int(n) for n in re.findall(r"\bline\s+(\d+)\b", clean_block)]
    if not numbers:
        return ""
    header = f"[{idx}] line {max(numbers)}:"
    body = "\n".join(f"  {line}" for line in clean_block.splitlines())
    return f"{header}\n{body}"


class build_error_message_extractor:
    """
    Alternative extractor that identifies blocks solely based on
    "*** At command ..." lines, discarding segments that do not match
    the current session/theory.
    """

    def __init__(self):
        pass

    def extract_build_error_message(self, build_output: str, session: str, theory: str) -> str:
        if "*** Timeout" in build_output:
            raise TimeoutError("*** Timeout detected in build output")

        theory_tail = f"/{session}/{theory}".replace("\\", "/")

        segments: List[str] = []
        for part in build_output.split("***")[1:]:
            seg = part.strip()
            if seg:
                segments.append(seg)

        raw_blocks: List[str] = []
        current_block: List[str] = []

        for seg in segments:
            current_block.append(seg)
            if seg.startswith("At command"):
                loc = _parse_location(seg)
                if loc and loc[1].endswith(theory_tail):
                    raw_blocks.append("\n".join(current_block))
                current_block = []

        if not raw_blocks:
            return build_output.strip()

        seen = set()
        unique_blocks: List[str] = []
        for block in raw_blocks:
            key = block.splitlines()[0]
            if key not in seen:
                seen.add(key)
                unique_blocks.append(block)

        formatted = [
            _format_block(idx + 1, block)
            for idx, block in enumerate(unique_blocks)
        ]
        formatted = [blk for blk in formatted if blk]

        if not formatted:
            return build_output.strip()

        return "\n\n".join(formatted)
