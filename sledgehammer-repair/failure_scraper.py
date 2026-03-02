from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple, List
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from setup import *

def scrape_failed_session_theories(xlsx_path: str) -> List[Tuple[str, str]]:
    """
    Read an Excel file with at least:
      session+theory | fail/success

    Rule:
      - Group by session+theory.
      - If ANY row in a group is "success" (case-insensitive), the group is considered success.
      - Only return keys whose group has NO "success" rows (i.e., all failed / non-success).

    Parsing:
      - For "CZH_Foundations/czh_sets/CZH_Sets_ZQR.thy":
          session = "CZH_Foundations/czh_sets"
          theory  = "CZH_Sets_ZQR.thy"
      - If there is no "/", session = "" and theory = whole string.
    """

    df = pd.read_excel(xlsx_path, dtype=str, engine="openpyxl")

    def norm(s: str) -> str:
        return "".join(ch.lower() for ch in s.strip() if ch not in " \t\r\n")

    cols = {norm(c): c for c in df.columns}

    for r in ["session+theory", "fail/success"]:
        if norm(r) not in cols:
            raise KeyError(f'Missing required column "{r}". Found columns: {list(df.columns)}')

    col_st = cols[norm("session+theory")]
    col_status = cols[norm("fail/success")]

    # Normalize fields
    st = df[col_st].fillna("").astype(str).str.strip()
    status = df[col_status].fillna("").astype(str).str.strip().str.lower()

    # Define "success" rows (treat anything else as non-success)
    is_success = status.eq("success")

    # For each session+theory: does it have ANY success?
    any_success_by_key = is_success.groupby(st).any()

    # Keys that are overall-fail: no success entries
    failed_keys = any_success_by_key[~any_success_by_key].index.tolist()

    def split_session_theory(path: str) -> Tuple[str, str]:
        p = str(path).strip()
        if "/" not in p:
            return "", p
        session, theory = p.rsplit("/", 1)
        return session, theory

    result: List[Tuple[str, str]] = [split_session_theory(k) for k in failed_keys]

    print(result)
    return result


scrape_failed_session_theories(XLSX_PATH)