from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union
import shutil
import subprocess
import time
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI

from failure_extractor import failure_extractor
from build_error_message_extractor import *
from setup import API_KEY, SOURCE_AFP, TARGET_AFP, TARGET_ISABELLE, XLSX_PATH
from excel_creater import excel_creater


ReplacementBlock = Tuple[int, List[str]]  # (start line, replacement lines)


def _root_session_name(session_path: str) -> str:
    """
    Extract the top-level session name from a potentially nested relative path.
    Example: "CakeML_Codegen/Backend" -> "CakeML_Codegen".
    """
    normalized = session_path.replace("\\", "/").lstrip("/")
    if not normalized:
        return session_path
    return normalized.split("/", 1)[0]


class llm_repairer_multiline:
    """
    Variant of llm_repairer that allows the LLM to rewrite contiguous blocks
    using the header format `LINES N-M:`. Each block must contain exactly
    (M - N + 1) replacement lines.
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------
    def run_llm(self, error_message: str, error_snippet: str, additional_info = None) -> str | None:
        openai_model = "gpt-5.2"
        prompt = f"""You are an Isabelle proof engineer.
        This Isabelle/AFP 2023 theory no longer builds under Isabelle/AFP 2024.
        Given the error message and the relevant snippet, propose fixes that make it build on Isabelle 2024.

        How to read the inputs:
        - The *error message* consists of entries like "[k] line N: <description>" which identify the failing line.
        - The *erroneous snippet* shows the local context ending exactly at the failing line, and each failing line is marked with "(* line N *)".

        Critical requirements (MUST FOLLOW):
        1) For each issue, output exactly one block headed by "LINES N-M:" where N and M are inclusive line numbers (use "LINES N-N:" for single-line fixes).
        2) Immediately after the header, provide the complete replacement lines for that range, preserving order. If N=M, the block must contain exactly one line. If N≠M, the block must contain exactly (M - N + 1) lines. Never add blank lines inside a block.
        Extremely important to provide the same number of lines as you specified, otherwise cannot be parsed. 
        3) Output strictly ASCII text; no explanations or comments.
        4) Separate consecutive blocks with one blank line.
        5) Do not emit anything else besides the blocks, and never include more or fewer lines than the specified range (M - N + 1).

        Error message:
        {error_message}

        Erroneous snippet:
        {error_snippet}
        """
        if additional_info:
            prompt += f"""

        Dependency changes (IMPORTANT — read carefully):
        The following shows how the Isabelle/HOL library lemmas that this proof
        depends on have changed between Isabelle 2023 and 2024.  These changes
        are the likely ROOT CAUSE of the breakage.

        How to read this section:
          - It is grouped by "[k] line N:" blocks that correspond exactly to the
            same "[k] line N:" entries in the error message above — so the changes
            under "[1] line 87:" are the dependencies relevant to error [1].
          - "dep: <qualified name> @ <file>:<line>" identifies the changed lemma.
          - "cmd lines: N-M" is the line range of the change.
          - The unified diff (@@...) shows exactly what was added (+) or removed (-).

        Use these diffs to understand WHAT changed in the library (renamed lemmas,
        changed type-class constraints, new required parentheses, reordered
        arguments, etc.) and apply the corresponding fix to the erroneous snippet.

        {additional_info}"""

        client = OpenAI(api_key=API_KEY)
        response = client.chat.completions.create(
            model=openai_model,
            messages=[{"role": "system", "content": prompt}],
        )
        return response.choices[0].message.content

    # ------------------------------------------------------------------
    # Parsing / applying fixes
    # ------------------------------------------------------------------
    def parse_llm_fixes(self, llm_output: str) -> List[ReplacementBlock]:
        """
        Parse output consisting of blocks that look like:

        LINES N-M:
        <replacement line N>
        ...
        <replacement line M>
        """
        blocks = []
        raw_blocks = [b.strip("\n") for b in llm_output.strip().split("\n\n") if b.strip()]
        for block in raw_blocks:
            lines = block.splitlines()
            if not lines:
                continue

            header = lines[0].strip()
            if not header.startswith("LINES ") or not header.endswith(":"):
                raise ValueError(f"Bad fix header: {header!r}")

            range_part = header[len("LINES ") : -1].strip()
            if "-" not in range_part:
                raise ValueError(f"Expected range 'N-M' in header: {header!r}")
            start_str, end_str = [x.strip() for x in range_part.split("-", 1)]
            if not (start_str.isdigit() and end_str.isdigit()):
                raise ValueError(f"Non-numeric range in header: {header!r}")

            start = int(start_str)
            end = int(end_str)
            if start > end:
                raise ValueError(f"Invalid range (start > end) in header: {header!r}")

            payload = lines[1:]
            expected_len = end - start + 1
            if len(payload) != expected_len:
                raise ValueError(
                    f"Block {header!r} expects {expected_len} replacement lines; got {len(payload)}"
                )

            blocks.append((start, payload))

        return blocks

    def apply_fixes(
        self,
        session: str,
        theory: str,
        replacements: Sequence[ReplacementBlock],
    ) -> str:
        """
        Apply block replacements to TARGET_AFP/<session>/<theory>.
        """
        target_file_path = Path(TARGET_AFP).joinpath(session, theory)

        original_text = target_file_path.read_text(encoding="utf-8", errors="replace")
        lines = original_text.splitlines()
        n = len(lines)

        for start, block_lines in replacements:
            block_len = len(block_lines)

            # start must point to an existing line (1-indexed).
            if not (1 <= start <= n):
                raise ValueError(f"Start line {start} out of range for file with {n} lines")

            # Replace from start onwards. If block_lines is longer than the
            # remaining lines, Python slice assignment naturally appends the extra
            # lines at the end of the file.
            start_idx = start - 1
            lines[start_idx:start_idx + block_len] = block_lines

        patched_text = "\n".join(lines) + ("\n" if original_text.endswith("\n") else "")
        target_file_path.write_text(patched_text, encoding="utf-8")
        return str(target_file_path)

    # ------------------------------------------------------------------
    # File management (copied from original repairer)
    # ------------------------------------------------------------------
    def backup_and_copy(self, session: str, theory: str) -> None:
        source_file_path = Path(SOURCE_AFP).joinpath(session, theory)
        target_dir = Path(TARGET_AFP).joinpath(session)
        target_file_path = target_dir.joinpath(theory)

        if not source_file_path.is_file():
            raise FileNotFoundError(f"Cannot find source theory file: {source_file_path}")

        if not target_file_path.is_file():
            raise FileNotFoundError(f"Cannot find target theory file: {target_file_path}")

        stem = Path(theory).stem
        original_backup = target_dir.joinpath(f"{stem}_original.thy")
        if not original_backup.exists():
            target_file_path.rename(original_backup)

        shutil.copy2(source_file_path, target_file_path)

    def restore(self, session: str, theory: str) -> None:
        target_dir = Path(TARGET_AFP).joinpath(session)
        target_file_path = target_dir.joinpath(theory)
        stem = Path(theory).stem
        original_backup = target_dir.joinpath(f"{stem}_original.thy")

        if target_file_path.exists():
            target_file_path.unlink()
        if original_backup.exists():
            original_backup.rename(target_file_path)

    # ------------------------------------------------------------------
    # Build + high-level runner (mirrors original behavior)
    # ------------------------------------------------------------------
    def build(self, session: str) -> str:
        cmd = [TARGET_ISABELLE, "build", "-v", "-d", TARGET_AFP, session]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            return f"Build failed: {e}"

        if proc.returncode == 0:
            return "success"

        error_output = proc.stdout.strip()
        if proc.stderr.strip():
            error_output += "\n\n" + proc.stderr.strip()
        return error_output

    def repair_session_theory(
        self,
        session: str,
        theory: str,
        max_attempts: int = 3,
        max_chars: int = 20000,
        additional_info: str | None = None,
        sheet_name: str = "llm-repair",
    ) -> Dict[str, Union[str, int]]:
        fe = failure_extractor()
        last_error_message = ""
        last_build_output = ""
        start_time = time.monotonic()
        backed_up = False

        print(f"[repair-multi] session={session} theory={theory} max_attempts={max_attempts}")

        try:
            for attempt in range(1, max_attempts + 1):
                print(f"\n[repair-multi] attempt {attempt}/{max_attempts} for {session}/{theory}")

                build_out = ""
                result = None
                status = "fail"
                error_message = ""
                llm_output = ""

                try:
                    if attempt == 1:
                        self.backup_and_copy(session, theory)
                        backed_up = True
                        print("[repair-multi] extracting initial error message from SOURCE_AFP footer comment")
                        error_message = fe.extract_error_message(session, theory)
                    else:
                        print("[repair-multi] extracting error message from last build output")
                        fe_build = build_error_message_extractor()
                        error_message = fe_build.extract_build_error_message(last_build_output, session, theory)
                        if not error_message.strip():
                            print("[repair-multi] no target-theory errors in build output (cascade only); reusing last known error")
                            error_message = last_error_message

                    last_error_message = error_message
                    print(error_message)

                    error_lines = fe.extract_lines(error_message)
                    # if not error_lines:
                    #     raise ValueError("No error lines parsed from error message")
                    print(f"[repair-multi] parsed error lines: {error_lines}")

                    error_snippet = fe.extract_erroneous_snippet(
                        session, theory, error_lines, max_chars=max_chars
                    )

                    if not error_snippet:
                        raise ValueError("No error snippet parsed from theory file")

                    print("[repair-multi] calling LLM for block replacements")
                    llm_output = self.run_llm(error_message=error_message, error_snippet=error_snippet, additional_info=additional_info)

                    replacements = self.parse_llm_fixes(llm_output) # type: ignore
                    print(f"[repair-multi] parsed {len(replacements)} replacement blocks from LLM output")
                    self.apply_fixes(session=session, theory=theory, replacements=replacements)

                    build_target = _root_session_name(session)
                    print(f"[repair-multi] building root session {build_target} with patched theory")
                    build_out = self.build(build_target)
                    last_build_output = build_out

                    if build_out == "success":
                        print(f"[repair-multi] SUCCESS on attempt {attempt}: {session}/{theory}")
                        status = "success"
                        result = {
                            "status": status,
                            "attempts": attempt,
                            "last_error_message": last_error_message,
                            "last_build_output": "",
                        }
                    else:
                        print("[repair-multi] build failed after applying fixes (will retry if attempts remain)")
                        if attempt == max_attempts:
                            result = {
                                "status": "failed",
                                "attempts": attempt,
                                "last_error_message": last_error_message,
                                "last_build_output": last_build_output,
                            }
                except TimeoutError as e:
                    last_error_message = "*** Timeout"
                    if not last_build_output:
                        last_build_output = error_message or str(e)
                    print("[repair-multi] detected build timeout; aborting repairs for this theory")
                    result = {
                        "status": "failed",
                        "attempts": attempt,
                        "last_error_message": last_error_message,
                        "last_build_output": last_build_output,
                    }
                except Exception as e:
                    last_build_output = f"Internal pipeline error: {e!r}"
                    print(f"[repair-multi] pipeline exception: {e!r} (will retry if attempts remain)")
                finally:
                    try:
                        is_final_attempt = (
                            attempt == max_attempts or build_out == "success" or result is not None
                        )
                        elapsed_seconds = time.monotonic() - start_time if is_final_attempt else None
                        ec = excel_creater()
                        ec.append_row(
                            session=session,
                            theory=theory,
                            attempt=attempt,
                            error_message=(error_message.strip() if error_message else last_error_message.strip()),
                            fixes_text=llm_output.strip(), # type: ignore
                            status=status,
                            elapsed_seconds=elapsed_seconds,
                            sheet_name=sheet_name,
                        )
                        print(f"[repair-multi] wrote attempt {attempt} row to {XLSX_PATH}")
                    except Exception as e:
                        print(f"[repair-multi] WARNING: failed to write excel log: {e!r}")

                if attempt == max_attempts or build_out == "success" or result is not None:
                    print("[repair-multi] restoring theory state")
                    try:
                        self.restore(session=session, theory=theory)
                        backed_up = False
                        print("[repair-multi] restored target theory state")
                    except Exception as e:
                        restore_err = f"Restore failed: {e!r}"
                        print(f"[repair-multi] {restore_err}")
                        return {
                            "status": "failed",
                            "attempts": attempt,
                            "last_error_message": last_error_message,
                            "last_build_output": (last_build_output + "\n\n" + restore_err).strip(),
                        }

                if result is not None:
                    return result

            print(f"\n[repair-multi] FAILED after {max_attempts} attempts: {session}/{theory}")
            return {
                "status": "failed",
                "attempts": max_attempts,
                "last_error_message": last_error_message,
                "last_build_output": last_build_output,
            }
        finally:
            # Guaranteed restore on abnormal exit (e.g. KeyboardInterrupt).
            if backed_up:
                print("[repair-multi] restoring theory state (interrupted)")
                try:
                    self.restore(session=session, theory=theory)
                except Exception as e:
                    print(f"[repair-multi] Restore failed: {e!r}")
