from pathlib import Path
from openai import OpenAI
from typing import Dict, List, Union
import shutil
import subprocess

from failure_extractor import *
from setup import *
from excel_creater import *

class llm_repairer:
    def __init__(self):
        pass

    def run_llm(self, error_message: str, error_snippet: str) -> str:
        openai_model = "gpt-5"
        prompt = f"""You are an Isabelle proof engineer.
        This Isabelle/AFP 2023 theory no longer builds under Isabelle/AFP 2024.
        Given the error message and the relevant snippet, propose fixes that make it build on Isabelle 2024.

        How to read the inputs:
        - The *error message* consists entries of the form "[k] line N:  specific error message", which precisely identifies the source line N in the .thy file that failed and why.
        - The *erroneous snippet* shows the local declaration/proof context extracted from the original file, ending exactly at the erroneous line (line N).
        - Each erroneous line in the snippet is marked inline with "(* line N *)".

        Critical requirement (MUST FOLLOW):
        1) For each erroneous line number N, you must output ONE COMPLETE SINGLE LINE that can be substituted verbatim for the original line N in the .thy file.
        Output exactly one line of replacement text per line number.
        2) Do NOT change the meaning of the proof. Only fix compatibility / missing facts / proof steps.
        3) Use ASCII characters only. DO NOT use Unicode. Output must be plain ASCII.
        4) Output ONLY the fixed versions of the erroneous lines (no explanations, not whole proof, no extra text).
        5) For each erroneous line number N, output format must be:

        LINE N:
        <one complete corrected line replacing original line N>

        6) Separate each block by exactly one blank line.
        7) Do not output anything else.

        Error message:
        {error_message}

        Erroneous snippet (each erroneous line has a marker "(* line N *)" at the end):
        {error_snippet}
        """

        client = OpenAI(api_key = API_KEY)
        response = client.chat.completions.create(
            model=openai_model,
            messages=[
                {"role": "system", "content": prompt},
            ],
        )

        return response.choices[0].message.content
    


    def parse_llm_fixes(self, llm_output: str) -> Dict[int, str]:
        """
        Parse LLM output of the form:

        LINE 395:
        <one complete corrected line>

        (blocks separated by blank lines)

        Returns: {395: "<replacement line>", ...}
        """
        fixes: Dict[int, str] = {}
        blocks = [b.strip("\n") for b in llm_output.strip().split("\n\n") if b.strip()]
        for block in blocks:
            lines = block.splitlines()
            if not lines:
                continue
            header = lines[0].strip()
            if not header.startswith("LINE ") or not header.endswith(":"):
                raise ValueError(f"Bad fix header: {header!r}")

            num_str = header[len("LINE ") : -1].strip()
            if not num_str.isdigit():
                raise ValueError(f"Bad line number in header: {header!r}")
            line_number = int(num_str)

            # Replacement must be a single line. If LLM emitted multiple lines, join with spaces.
            replacement = " ".join(s.strip() for s in lines[1:] if s.strip() != "")
            if not replacement:
                raise ValueError(f"Empty replacement for line {line_number}")

            fixes[line_number] = replacement
        return fixes

    
    def backup_and_copy(self, session:str, theory: str) -> None:
        """
        Prepare TARGET_AFP/<session>/<theory> for patching.

        Steps:
        1) Ensure SOURCE_AFP/<session>/<theory> exists.
        2) Ensure TARGET_AFP/<session> exists (create if needed).
        3) If TARGET_AFP/<session>/<theory> exists:
             - remove any existing <stem>_original.thy
             - rename <theory> -> <stem>_original.thy
           Else:
             - do nothing (no rename), we are creating the file fresh.
        4) Copy SOURCE file to TARGET file (fresh working copy).
        """
        source_file_path = Path(SOURCE_AFP).joinpath(session, theory)
        target_dir = Path(TARGET_AFP).joinpath(session)
        target_file_path = target_dir.joinpath(theory)

        if not source_file_path.is_file():
            raise FileNotFoundError(f"Cannot find source theory file: {source_file_path}")
        
        if not target_file_path.is_file():
            raise FileNotFoundError(f"Cannot find target theory file: {target_file_path}")
        
        stem = Path(theory).stem  # stem is the file name without final suffix
        original_backup = target_dir.joinpath(f"{stem}_original.thy")
        if original_backup.exists():
            pass  # delete it
        else:
            target_file_path.rename(original_backup)

        # Copy source to target
        shutil.copy2(source_file_path, target_file_path)


    def apply_fixes(self, session:str, theory: str, error_lines: List[int], fixes: Dict[int, str]) -> str:
        """
        - Applies replacements for each line number in error_lines (1-based) in the copied file
        - Returns the path to the patched target file
        """
        target_file_path = Path(TARGET_AFP).joinpath(session, theory)

        # Load target content
        original_text = target_file_path.read_text(encoding="utf-8", errors="replace")
        lines = original_text.splitlines()
        n = len(lines)

        # Apply fixes only for requested error_lines
        for line in error_lines:
            if line not in fixes:
                raise ValueError(f"Missing fix for error line {line}")
            if not (1 <= line <= n):
                raise ValueError(f"Error line {line} out of range (file has {n} lines)")
            lines[line - 1] = fixes[line]  # line number is 1 indexed

        patched_text = "\n".join(lines) + ("\n" if original_text.endswith("\n") else "")
        target_file_path.write_text(patched_text, encoding="utf-8")

        return str(target_file_path)


    def build(self, session: str) -> str:
        """
        build the target session

        :return: success if the build is successful, else the error message
        :rtype: str
        """
        
        cmd = [
            TARGET_ISABELLE,
            "build",
            "-v",
            "-d",
            TARGET_AFP,
            session,
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,  # capture stdout instead of printing to terminal
                stderr=subprocess.PIPE,
                text=True,
                check=False,   # do NOT raise on non-zero exit
            )
        except FileNotFoundError as e:
            return f"Build failed: {e}"
        
        if proc.returncode == 0:
            return "success"

        # Isabelle writes most errors to stdout, sometimes stderr
        error_output = proc.stdout.strip()
        if proc.stderr.strip():
            error_output += "\n\n" + proc.stderr.strip()
        return error_output
    
    
    def restore(self, session: str, theory: str) -> None:
        """
        Restore the target theory file:
        - delete patched TARGET_AFP/<session>/<theory>
        - rename TARGET_AFP/<session>/<stem>_original.thy back to <theory>
        """
        target_dir = Path(TARGET_AFP).joinpath(session)
        target_file_path = target_dir.joinpath(theory)
        stem = Path(theory).stem
        original_backup = target_dir.joinpath(f"{stem}_original.thy")

        # remove patched file if it exists
        if target_file_path.exists():
            target_file_path.unlink()

        # restore original backup
        if original_backup.exists():
            original_backup.rename(target_file_path)

    
    def repair_session_theory(       
        self,
        session: str,
        theory: str,
        max_attempts: int = 3,
        max_chars: int = 16000,
    ) -> Dict[str, Union[str, int]]:
        
        fe = failure_extractor()

        last_error_message = ""
        last_build_output = ""

        print(f"[repair] session={session} theory={theory} max_attempts={max_attempts}")

        for attempt in range(1, max_attempts + 1):
            print(f"\n[repair] attempt {attempt}/{max_attempts} for {session}/{theory}")

            result = None
            status = "fail"  
            # 1) Get error message + line numbers + snippet (source comment for attempt1, build log for later attempts)
            if attempt == 1:
                self.backup_and_copy(session, theory)
                print("[repair] extracting initial error message from SOURCE_AFP theory footer comment")
                error_message = fe.extract_error_message(session, theory)
            else:
                print("[repair] extracting error message from last build output")
                # print(last_build_output)
                error_message = fe.extract_build_error_message(last_build_output, session, theory)

            last_error_message = error_message
            print(error_message)
            error_lines = fe.extract_lines(error_message)
            print(f"[repair] parsed error lines: {error_lines}")
            error_snippet = fe.extract_erroneous_snippet(session, theory, error_lines, max_chars=max_chars)
            print("[repair] extracting erroneous snippet")
            # print(error_snippet)

            # 2) Ask LLM
            print("[repair] calling LLM for line replacements")
            llm_output = self.run_llm(error_message=error_message, error_snippet=error_snippet)

            # 3) Parse + apply fixes + build + restore
            try:
                fixes = self.parse_llm_fixes(llm_output)
                print(fixes)
                print(f"[repair] parsed {len(fixes)} replacements from LLM output")
                self.apply_fixes(session=session, theory=theory, error_lines=error_lines, fixes=fixes)

                print("[repair] building session with patched theory")
                build_out = self.build(session)
                last_build_output = build_out

                if build_out == "success":
                    print(f"[repair] SUCCESS on attempt {attempt}: {session}/{theory}")
                    status = "success"  
                    result =  {
                        "status": status,
                        "attempts": attempt,
                        "last_error_message": last_error_message,
                        "last_build_output": "",
                    }
                else:
                    print("[repair] build failed after applying fixes (will retry if attempts remain)")
                    status = "fail"  
                    if attempt == max_attempts:
                        result = {
                        "status": "failed",
                        "attempts": attempt,
                        "last_error_message": last_error_message,
                        "last_build_output": last_build_output,
                    }
            except Exception as e:
                last_build_output = f"Internal pipeline error: {e!r}"
                print(f"[repair] pipeline exception: {e!r} (will retry if attempts remain)")
            finally:
                try:
                    ec = excel_creater()
                    ec.append_row(
                        session=session,
                        theory=theory,
                        attempt=attempt,
                        error_message=error_message.strip(),
                        fixes_text=llm_output.strip(),
                        status=status,
                    )
                    print(f"[repair] wrote attempt {attempt} row to {XLSX_PATH}")
                except Exception as e:
                    print(f"[repair] WARNING: failed to write excel log: {e!r}")

            if attempt == max_attempts or build_out == "success":
                print("[repair] restoring theory state")
                try:
                    self.restore(session=session, theory=theory)
                    print("[repair] restored target theory state")
                except Exception as e:
                    restore_err = f"Restore failed: {e!r}"
                    print(f"[repair] {restore_err}")
                    # restoration failure is fatal; override result
                    return {
                        "status": "failed",
                        "attempts": attempt,
                        "last_error_message": last_error_message,
                        "last_build_output": (last_build_output + "\n\n" + restore_err).strip(),
                    }
                    
            if result is not None:
                return result
        
        print(f"\n[repair] FAILED after {max_attempts} attempts: {session}/{theory}")
        return {
            "status": "failed",
            "attempts": max_attempts,
            "last_error_message": last_error_message,
            "last_build_output": last_build_output,
        }


