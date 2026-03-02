import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from setup import MIRABELLE_OUTPUT, TARGET_AFP, TARGET_ISABELLE

def _root_session_name(session_path: str) -> str:
    """
    Extract the top-level session name from a potentially nested relative path.
    Example: "CakeML_Codegen/Backend" -> "CakeML_Codegen".
    """
    normalized = session_path.replace("\\", "/").lstrip("/")
    if not normalized:
        return session_path
    return normalized.split("/", 1)[0]

class mirabelle_runner:
    def __init__(self):
        pass

    def run_mirabelle(self, session: str, theory: str, line: int):
        theory_name = Path(theory).stem # trim the .thy suffix
        out_dir = Path(MIRABELLE_OUTPUT).joinpath(session, theory_name)
        root_session = _root_session_name(session)

        cmd = [
            TARGET_ISABELLE, "mirabelle",
            "-d", TARGET_AFP,
            "-O", out_dir,
            "-o", "quick_and_dirty=true",
            "-T", f"{session}.{theory_name}[{line}:{line + 1}]",
            "-A", "sledgehammer[timeout = 300]",
            root_session
        ]

        proc = subprocess.run(
            cmd,
            # stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        # Mirabelle writes the real results into mirabelle.log in -O dir.
        log_path = out_dir.joinpath("mirabelle.log")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Mirabelle failed with return code {proc.returncode}"
            )

        if not log_path.exists():
            raise FileNotFoundError(
                f"Mirabelle finished successfully, but log file not found: {log_path}"
            )

        return log_path


