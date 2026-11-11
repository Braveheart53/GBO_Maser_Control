#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Audit of the LuaLaTeX manual's documentation rules.

===============================================================================
 Title          : Manual Audit
 Filename       : tools/manual_audit.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-11
 Modified       : 2026-11-11
 Language       : Python 3.12 (standard library only)
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Workbench 0.3.0: checks that every display equation
                  block is preceded, in its section, by a variable table
                  (environment ``vartable``); that every equation is
                  numbered and cross-referenced in Appendix B; that every
                  table is captioned and listed; that the contents list the
                  lists of tables and figures and the appendices.
===============================================================================

Usage (after three ``lualatex`` runs, before deleting the .lot/.lof/.toc):

    python tools/manual_audit.py docs/MHM2010_T4_Workbench_Manual_v0.3.0

The argument is the path without the extension. Exit status 1 when a rule
fails. Nothing is written.
"""
from __future__ import annotations

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from pathlib import Path
import re
import sys

__version__ = "0.0.1"


# %% ===========================================================================
#    AUDIT
# =============================================================================
def audit(stem: str) -> list[str]:
    """Return the list of failed rules (empty when the manual passes)."""
    source = Path(stem + ".tex").read_text(encoding="utf-8")
    body = source[source.index(r"\begin{document}"):]
    failures: list[str] = []

    # 1. A variable table before every display equation block, in its section.
    events = [(m.start(), m.group(1), m.group(2))
              for m in re.finditer(r"\\(section|subsection|subsubsection)\{([^}]*)\}", body)]
    events += [(m.start(), "vartable", m.group(1))
               for m in re.finditer(r"\\begin\{vartable\}\{([^}]*)\}", body)]
    events += [(m.start(), "equation", m.group(0))           # "\[" but not a row "\\[5pt]"
               for m in re.finditer(r"\\begin\{(?:equation|align|gather)\*?\}|(?<!\\)\\\[", body)]
    events.sort()
    section, tables_in_section, blocks = None, [], 0
    for _, kind, text in events:
        if kind == "section":
            section, tables_in_section = text, []
        elif kind == "vartable":
            tables_in_section.append(text)
        elif kind == "equation":
            blocks += 1
            if not tables_in_section:
                failures.append(f"equation block without a variable table in section '{section}'")

    # 2. Every equation numbered (tag) and cited in Appendix B.
    tags = re.findall(r"\\tag\{([^}]*)\}", body)
    if len(re.findall(r"(?<!\\)\\\[", body)) or re.search(r"\\begin\{(?:equation|align|gather)\*\}", body):
        failures.append("unnumbered display equation")
    if len(tags) != len(set(tags)):
        failures.append("duplicate equation tag")
    appendix_b = body[body.index(r"\section{Appendix B"):body.index(r"\section{Appendix C")]
    cited = set(re.findall(r"\\eqref\{eq:([^}]*)\}", appendix_b))
    for letter, first, last in re.findall(r"\\eqref\{eq:([A-Z])(\d+)\}--\\eqref\{eq:[A-Z](\d+)\}", appendix_b):
        cited.update(f"{letter}{k}" for k in range(int(first), int(last) + 1))
    missing = sorted(set(tags) - cited)
    if missing:
        failures.append(f"equations not in Appendix B: {missing}")

    # 3. Tables captioned and listed; lists and appendices in the contents.
    captioned = (len(re.findall(r"\\begin\{ctable\}", body)) + len(re.findall(r"\\begin\{vartable\}", body))
                 + len(re.findall(r"\\begin\{longtable\}", body)))
    listed = Path(stem + ".lot").read_text(encoding="utf-8").count(r"\contentsline {table}")
    if listed != captioned:
        failures.append(f"list of tables has {listed} entries for {captioned} captioned tables")
    contents = Path(stem + ".toc").read_text(encoding="utf-8")
    for entry in ("List of tables", "List of figures", "Appendix A", "Appendix B", "Appendix C"):
        if entry not in contents:
            failures.append(f"contents lack '{entry}'")
    print(f"{blocks} equation blocks, {len(tags)} numbered equations, "
          f"{len(re.findall(r'begin{vartable}', body))} variable tables, {captioned} captioned tables "
          f"({listed} listed)")
    return failures


# %% ===========================================================================
#    COMMAND LINE
# =============================================================================
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    problems = audit(sys.argv[1])
    for problem in problems:
        print("FAIL:", problem)
    print("PASS" if not problems else f"{len(problems)} rule(s) failed")
    sys.exit(1 if problems else 0)
