import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from setup import TARGET_AFP

class handler:
    def __init__(self):
        pass
    
    def handle_apply_by(self, session: str, theory: str, line: int) -> int:
        """
        Look for a standalone `by` or `apply` on `line` (1-based) or the next line.
        On the line where it is found:
        - keep everything before `by/apply`
        - comment out everything from `by/apply` onward on that line
        - comment out all following consecutive lines that contain `by/apply`
        - append `apply - sorry` after the last such line
        Return the 1-based line number that was modified.
        """
        target_file_path = Path(TARGET_AFP).joinpath(session, theory)

        original_text = target_file_path.read_text(encoding="utf-8", errors="replace")
        lines = original_text.splitlines()
        n = len(lines)

        if line < 1 or line > n:
            raise IndexError(f"line {line} out of range (1..{n}) for {target_file_path}")

        token_pat = re.compile(r"\b(by|apply)\b")

        # candidates: current line, then next line if it exists
        candidates = [line - 1]
        if line < n:
            candidates.append(line)  # next line (0-based)

        found_idx = None
        m = None
        for idx in candidates:
            m = token_pat.search(lines[idx])
            if m:
                found_idx = idx
                break

        if found_idx is None or m is None:
            raise ValueError(
                f"No `by` or `apply` found on line {line} or {line+1} in {target_file_path}"
            )

        # Find the first line with by/apply
        first_idx = found_idx
        old_line = lines[first_idx]
        indent = old_line[: len(old_line) - len(old_line.lstrip())]

        prefix = old_line[:m.start()].rstrip()
        suffix = old_line[m.start():].strip()

        # Comment out the first line
        if prefix.strip():
            lines[first_idx] = f"{prefix} (* {suffix} *)"
        else:
            lines[first_idx] = f"{indent}(* {suffix} *)"
        
        print(f"[handler] Commented line {first_idx + 1}: {lines[first_idx]}")

        # Find and comment out all following consecutive lines with by/apply
        last_idx = first_idx
        for idx in range(first_idx + 1, n):
            if token_pat.search(lines[idx]):
                # This line contains by/apply, comment it out
                current_line = lines[idx]
                current_indent = current_line[: len(current_line) - len(current_line.lstrip())]
                content = current_line.strip()
                lines[idx] = f"{current_indent}(* {content} *)"
                print(f"[handler] Commented line {idx + 1}: {lines[idx]}")
                last_idx = idx
            else:
                # No by/apply on this line, stop looking
                break

        # Append `apply - sorry` after the last commented line
        lines[last_idx] = lines[last_idx] + f" apply - sorry"
        print(f"[handler] Final line {last_idx + 1}: {lines[last_idx]}")

        new_text = "\n".join(lines)
        if original_text.endswith("\n"):
            new_text += "\n"
        target_file_path.write_text(new_text, encoding="utf-8")

        return last_idx + 1
    
    def handle_proof_qed(self, session: str, theory: str, line: int) -> int:
        target_file_path = Path(TARGET_AFP).joinpath(session, theory)

        original_text = target_file_path.read_text(encoding="utf-8", errors="replace")
        lines = original_text.splitlines()
        n = len(lines)
        if line < 1 or line > n:
            raise IndexError(f"line {line} out of range (1..{n}) for {target_file_path}")

        proof_stack = []
        blocks = []
        proof_re = re.compile(r"\bproof\b")
        qed_re = re.compile(r"\bqed\b")

        for idx, content in enumerate(lines):
            if proof_re.search(content):
                proof_stack.append(idx)
            if qed_re.search(content) and proof_stack:
                start = proof_stack.pop()
                blocks.append((start, idx))

        target_idx = line - 1
        block = next(((s, e) for s, e in blocks if s <= target_idx <= e), None)
        if block is None:
            raise ValueError(f"No proof/qed block covers line {line} in {target_file_path}")

        start, end = block
        block_lines = lines[start : end + 1]
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]

        # Put "apply - sorry" and the opening comment on the same line,
        # then place the original 'proof ...' immediately after "(* ".
        first = block_lines[0].lstrip()  # e.g. "proof -" (without leading indentation)

        replacement = [f"{indent}apply - sorry (* {first}"]

        # Keep the middle lines exactly as they were.
        if len(block_lines) > 2:
            replacement.extend(block_lines[1:-1])

        # Append the closing comment to the last line (e.g. "qed" -> "qed *)").
        last = block_lines[-1].rstrip("\n")
        replacement.append(f"{last} *)")

        lines[start : end + 1] = replacement

        print(f"[handler] Replaced proof/qed block starting at line {start + 1}.")

        new_text = "\n".join(lines)
        if original_text.endswith("\n"):
            new_text += "\n"
        target_file_path.write_text(new_text, encoding="utf-8")

        return start + 1
