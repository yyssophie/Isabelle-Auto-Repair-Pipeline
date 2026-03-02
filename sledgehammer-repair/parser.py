from typing import List, Dict, Tuple
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm-repair"))

from setup import *
from excel_creater import *

class parser:
    def __init__(self):
        pass

    def parse_error_merssage(self, error_message: str) -> List[str]:
        """
        Split the error message into blocks keyed by "[k] line N:" headers.
        Return each block as a full string, including the header.
        """
        header_pattern = re.compile(r"\[\d+\]\s+line\s+\d+:")

        matches = list(header_pattern.finditer(error_message))
        if not matches:
            return []

        blocks: List[str] = []

        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(error_message)
            block_text = error_message[start:end].strip()
            blocks.append(block_text)

        return blocks

    def parse_mirabelle_log(self, log_path: Path, lines: List[int]) -> Tuple[Dict[int, str], str]:
        """
        Return:
          - fixes: { line_number -> "by (...)" or "Try this: ..." content }
          - status: "success" iff ALL requested line_numbers have a successful entry
        """
        if not log_path.exists():
            raise FileNotFoundError(f"Mirabelle log not found: {log_path}")

        log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        log_lines = log_text.splitlines()

        fixes: Dict[int, str] = {}
        all_found = True

        for ln in lines:
            # Match either " ... 82:2411 ..." or "... line 82 ..."
            pat_colon = re.compile(rf"\b{ln}:\d+\b")
            pat_line = re.compile(rf"\bline\s+{ln}\b")

            found_for_ln = False
            best_fix: str | None = None

            for entry in log_lines:
                # First locate entries associated with that line number
                if not (pat_colon.search(entry) or pat_line.search(entry)):
                    continue

                # Now decide if this entry indicates success
                if "sledgehammer" not in entry or "goal.apply" not in entry:
                    continue

                if "Try this:" in entry:
                    # Capture everything after "Try this:"
                    best_fix = entry.split("Try this:", 1)[1].strip()
                    best_fix = re.sub(r"\s*\(\d+\s*ms\)\s*$", "", best_fix)
                    found_for_ln = True
                    break

                # # Sometimes Mirabelle logs only "succeeded" without a reconstruction line
                # if "succeeded" in entry:
                #     best_fix = best_fix or "succeeded (no reconstruction printed)"
                #     found_for_ln = True
                #     # do not break; keep scanning in case a later line contains "Try this:"
                #     continue

            if found_for_ln and best_fix is not None:
                fixes[ln] = best_fix
            else:
                all_found = False

        status = "success" if all_found else "fail"
        return fixes, status

# from auto_repair_pipeline.sledgehammer.attempt_writer import *
# def main():
#     p = parser()
#     fixes, status = p.parse_mirabelle_log(Path("/Users/yuanyusi/Desktop/Isabelle/isabelle_artifact/artifact/auto_repair_pipeline/sledgehammer/mirabelle_out/Actuarial_Mathematics/Examples/mirabelle.log"),
#                                           [87])
#     aw = attempt_writer()
#     aw.write_attempt_4("Actuarial_Mathematics", "Examples", """[1] line 87:
#     Failed to finish proof (line 87):
#     goal (1 subgoal):
#      1. \<lbrakk>ereal (- (b / a)) \<le> $\<psi>; (0::real) < t \<and> t < - (b / a); - (1::real) < a; a < (0::real); (0::real) < b; \<And>x::real. \<lbrakk>(0::real) < x; x < - (b / a)\<rbrakk> \<Longrightarrow> l differentiable at x; \<And>x::real. \<lbrakk>(0::real) \<le> x; x < - (b / a)\<rbrakk> \<Longrightarrow> l integrable_on {x..} \<and> $e`\<circ>_x = a * x + b\<rbrakk> \<Longrightarrow> total_finite
#     variables:
#     l :: real \<Rightarrow> real
#     a, b, t :: real
#     At command "by" (line 87)""", fixes, status)


# if __name__ == "__main__":
#     main()
