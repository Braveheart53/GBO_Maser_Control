#!/usr/bin/env bash
# =============================================================================
#  README_pdf.sh - render README.md to the bookmarked README.pdf (LuaLaTeX)
#  Version : 1.0.2   Created/Modified: 2026-10-27   Author: W. W. Wallace
#  Needs   : pandoc >= 3, LuaLaTeX (TeX Live), DejaVu fonts, fvextra, xurl
#  Usage   : cd <repo> && docs/tools/README_pdf.sh
# =============================================================================
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
pandoc "$repo/README.md" \
  -f markdown+gfm_auto_identifiers+pipe_tables+tex_math_dollars-implicit_figures \
  -o "$repo/README.pdf" --pdf-engine=lualatex \
  --lua-filter="$here/colwidths.lua" -H "$here/header.tex" \
  -V mainfont="DejaVu Serif" -V monofont="DejaVu Sans Mono" -V monofontoptions="Scale=0.80" \
  -V mathfont="Latin Modern Math" -V fontsize=10pt -V geometry:margin=1.7cm \
  -V colorlinks=true -V linkcolor=blue -V urlcolor=blue \
  -V hyperrefoptions=bookmarksopen=true -V hyperrefoptions=pdfpagemode=UseOutlines \
  -M title="AB Power Meter Monitor 1.6.3 - Deployment and Integration Guide" \
  -M author="William W. Wallace, NRAO/GBO" -M date="2026-10-27" --toc --toc-depth=2
echo "written: $repo/README.pdf"
