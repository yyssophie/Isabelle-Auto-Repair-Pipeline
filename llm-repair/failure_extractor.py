from pathlib import Path
from typing import List
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
