import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from setup import SOURCE_AFP, TARGET_AFP, SOURCE_ISABELLE, TARGET_ISABELLE

from session import glob_theory_file_with_session

DATA_DIR = Path(__file__).parent / "data"

source_isabelle_src_dir = Path(SOURCE_ISABELLE).parent.parent / "src"
target_isabelle_src_dir = Path(TARGET_ISABELLE).parent.parent / "src"
source_afp_dir = Path(SOURCE_AFP)
target_afp_dir = Path(TARGET_AFP)


def build_session_map(theories_dir: Path) -> dict[str, str]:
    """Build {absolute_thy_path_str: session_name} for all .thy files under theories_dir."""
    result = {}
    for thy, session_info in glob_theory_file_with_session(theories_dir, verbose=True):
        if session_info is not None:
            result[str(thy)] = session_info.name
    return result


def save_session_map(theories_dir: Path, out_path: Path) -> None:
    print(f"Building session map for {theories_dir} ...")
    mapping = build_session_map(theories_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(mapping, indent=2))
    print(f"  Saved {len(mapping)} entries → {out_path}")


def load_session_map(json_path: Path) -> dict[str, str]:
    """Load a pre-built session map. Returns {absolute_thy_path_str: session_name}."""
    return json.loads(json_path.read_text())


def build_all() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_session_map(source_isabelle_src_dir, DATA_DIR / "source_isabelle.json")
    save_session_map(target_isabelle_src_dir, DATA_DIR / "target_isabelle.json")
    save_session_map(source_afp_dir,          DATA_DIR / "source_afp.json")
    save_session_map(target_afp_dir,          DATA_DIR / "target_afp.json")


if __name__ == "__main__":
    build_all()
