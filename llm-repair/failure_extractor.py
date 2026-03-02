from pathlib import Path
from typing import List, Tuple
import json
import re
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from setup import TARGET_AFP, OUTPUT_ROOT


class failure_extractor:
    def __init__(self):
        pass


    def extract_error_message(self, session, theory) -> str:
        theory_stem = Path(theory).stem
        root_session = session.split("/")[0]
        json_filename = f"{session.replace('/', '@')}@{theory_stem}.json"
        json_path = Path(OUTPUT_ROOT) / root_session / json_filename

        if not json_path.is_file():
            raise FileNotFoundError(f"Cannot find JSON file: {json_path}")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        failures = data.get("failures", [])

        if not failures:
            raise ValueError(f"No failures found in {json_path}")

        lines = []
        for i, failure in enumerate(failures, start=1):
            pos = failure["pos"]
            msg = failure["msg"]
            lines.append(f"[{i}] line {pos}:")
            for msg_line in msg.splitlines():
                lines.append(f"  {msg_line}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
    

    # extract line number of the error lines from the error message
    def extract_lines(self, error_message: str) -> List[str]:
        """
        Split the error message into blocks keyed by "[k] line N:" headers.
        For each block, return the largest line number mentioned (including the header).
        """
        header_pattern = re.compile(r"\[\d+\]\s+line\s+(\d+):")
        matches = list(header_pattern.finditer(error_message))

        if not matches:
            return []

        result: List[int] = []

        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(error_message)
            block_text = error_message[start:end]

            numbers = [int(match.group(1))]
            numbers.extend(int(n) for n in re.findall(r"\bline\s+(\d+)\b", block_text))

            result.append(max(numbers))

        return result

    
    # extract error lines + preceeding context 
    def extract_erroneous_snippet(self, session, theory, error_lines: List[str], max_chars: int = 16000) -> str:
        """
        For each erroneous line:
          - scan backwards to the nearest preceding header line starting with:
            lemma/theorem/corollary/definition/fun/primrec
          - extract ONLY from that header up to the error line (plus optional context_before_error)
          - insert marker '(* line N *)' immediately before each erroneous line

        Constraint:
          - enforce max_chars on the final concatenated snippet 
        """
        file_path = Path(
            Path(TARGET_AFP).joinpath(session, theory)
        )
        if not file_path.is_file():
            raise FileNotFoundError(f"Cannot find theory file: {file_path}")
        
        text = file_path.read_text(encoding="utf-8", errors="replace")
        file_lines = text.splitlines()
        
        if len(error_lines) == 0:
            print("No error")
            content = "\n".join(file_lines)
            return content[:max_chars]

        
        max_char_per_error = max(1, max_chars // len(error_lines))

        header_re = re.compile(r"^\s*(lemma|theorem|corollary|definition|fun|primrec)(\s|$)")
        is_header = lambda s: header_re.match(s) is not None

        def find_prev_header(ln: int) -> int:
            for k in range(ln, 0, -1):
                if is_header(file_lines[k - 1]):
                    return k
            return None
        
        def shrink_block(lines_block: List[str], budget: int) -> str:
            if not lines_block:
                return ""
            
            # Keep the label line intact (line 0), trim only the body if needed.
            label = lines_block[0] + "\n"
            body = "\n".join(lines_block[1:])

            if len(label) >= budget:
                return label[:budget]

            remaining = budget - len(label)
            if len(body) <= remaining:
                return label + body

            # keep tail of the body (closest to error line)
            return label + body[-remaining:]
        
        
        blocks: List[str] = []
        
        for i in range(0, len(error_lines)):
            error_line_number = error_lines[i]
            header_line_number = find_prev_header(error_line_number)
            if header_line_number is not None:
                block_lines = file_lines[header_line_number - 1 : error_line_number]  # includes header..error line
            else:
                block_lines = file_lines[0 : error_line_number] 

            # add marker at END of error line
            if block_lines:
                block_lines.insert(0, f"Erroneous Snippet [{i + 1}]")
                block_lines[-1] = block_lines[-1] + f" (* line {error_line_number} *)"

            blocks.append(shrink_block(block_lines, max_char_per_error))

        if not any(blocks):
            content = "\n".join(file_lines)
            return content[:max_chars]

        return "\n\n".join(blocks)
        

    def extract_build_error_message(self, build_output: str, session: str, theory: str) -> str:
        """
        Extract an LLM-friendly error message from `isabelle build` output,
        restricted to a single target theory, with correct block boundaries.

        Block definition (for the target theory only):
        - START: a primary "*** ..." message that contains a source location
                (line N of ".../<session>/<theory>") and does NOT start with "At command".
        - END:   the corresponding "*** At command ..." message for the same theory
                (it also carries a source location).

        Notes:
        - Isabelle sometimes prints several "*** ..." segments concatenated on one
            physical line. We therefore split the entire output by occurrences of "***".
        - The build log may repeat the same error block multiple times (e.g. both a
            pretty multi-line form and a flattened one-liner). We de-duplicate by the
            (primary_line, primary_message_text) key.
        - We keep intermediate "*** ..." lines without a source location (e.g. goal
            lines) inside the current block.
        """

        if "*** Timeout" in build_output:
            raise TimeoutError("*** Timeout detected in build output")

        # Identify the target theory path suffix.
        theory_tail = f"/{session}/{theory}".replace("\\", "/")

        # Location pattern: NOT anchored to end, because flattened lines may have trailing text.
        loc_re = re.compile(r'\(line\s+(?P<ln>\d+)\s+of\s+"(?P<path>[^"]+)"\)')

        def parse_loc(seg: str) -> Tuple[int, str, int] | None:
            """
            If seg contains a source location, return (line_number, normalized_path, loc_end_index).
            loc_end_index is the index in seg right after the closing ')'.
            """
            m = loc_re.search(seg)
            if not m:
                return None
            ln = int(m.group("ln"))
            path = m.group("path").replace("\\", "/")
            return ln, path, m.end()

        def normalize_segment(seg: str) -> str:
            """
            Trim whitespace; if it contains a location, truncate at end of location
            so trailing garbage (e.g. 'Unfinished session(s)...') is discarded.
            """
            seg = seg.strip()
            loc = parse_loc(seg)
            if loc is None:
                return seg
            _ln, _path, end = loc
            return seg[:end].rstrip()

        # Tokenize: split entire output by "***" occurrences (works even if glued on one line).
        # The first chunk is before the first "***" and is ignored.
        raw_parts = build_output.split("***")
        segments: List[str] = []
        for part in raw_parts[1:]:
            seg = normalize_segment(part)
            if seg:
                segments.append(seg)

        blocks: List[List[str]] = []
        current: List[str] = []
        in_block = False

        seen_primary_keys = set()  # (primary_ln, primary_msg_text)

        def is_target(seg: str) -> bool:
            loc = parse_loc(seg)
            return loc is not None and loc[1].endswith(theory_tail)

        def is_at_command(seg: str) -> bool:
            return seg.startswith("At command")

        def primary_key(block: List[str]) -> Tuple[int, str] | None:
            """Compute (ln, msg_text_without_location) for the first line of a block."""
            if not block:
                return None
            loc = parse_loc(block[0])
            if loc is None:
                return None
            ln, _path, _end = loc
            # Remove the location suffix to get a stable message text.
            msg_text = loc_re.sub("", block[0]).rstrip(" :")
            return (ln, msg_text)

        def flush():
            nonlocal current, in_block
            if current:
                key = primary_key(current)
                if key is None or key not in seen_primary_keys:
                    if key is not None:
                        seen_primary_keys.add(key)
                    blocks.append(current)
            current = []
            in_block = False

        for seg in segments:
            if not in_block:
                # Start only on a TARGET primary error line (has location, not At command).
                if is_target(seg) and (not is_at_command(seg)):
                    current = [seg]
                    in_block = True
                else:
                    continue
            else:
                # Inside a block, keep all "*** ..." segments (including goal lines).
                current.append(seg)

                # End block exactly at the TARGET "At command ..." line.
                if is_target(seg) and is_at_command(seg):
                    flush()

        # If we ended without seeing At command, keep whatever we collected.
        if current:
            flush()

        if not blocks:
            return build_output.strip()

        # Format blocks into the error_message style your pipeline expects.
        out_lines: List[str] = []
        for i, blk in enumerate(blocks, start=1):
            # Choose "max line number" among segments that have a location for target theory.
            lns: List[int] = []
            for s in blk:
                loc = parse_loc(s)
                if loc is not None and loc[1].endswith(theory_tail):
                    lns.append(loc[0])
            max_ln = max(lns) if lns else -1

            out_lines.append(f"[{i}] line {max_ln if max_ln >= 0 else '?'}:")
            for s in blk:
                loc = parse_loc(s)
                if loc is not None:
                    ln, _path, _end = loc
                    cleaned = loc_re.sub("", s).rstrip(" :")
                    out_lines.append(f"  {cleaned} (line {ln})")
                else:
                    out_lines.append(f"  {s}")
            out_lines.append("")

        return "\n".join(out_lines).rstrip() + "\n"
