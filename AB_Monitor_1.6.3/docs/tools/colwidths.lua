-- colwidths.lua: give every table relative column widths proportional to the
-- longest cell text in each column (6..60 characters), so LaTeX wraps cells
-- inside the text width instead of letting wide tables run off the page.
function Table(tbl)
  local n = #tbl.colspecs
  local longest = {}
  for i = 1, n do longest[i] = 3 end
  local function scan(rows)
    for _, row in ipairs(rows) do
      for i, cell in ipairs(row.cells) do
        if i <= n then
          local len = utf8.len(pandoc.utils.stringify(cell.contents)) or 3
          if len > longest[i] then longest[i] = len end
        end
      end
    end
  end
  scan(tbl.head.rows)
  for _, body in ipairs(tbl.bodies) do scan(body.body) end
  local total = 0
  for i = 1, n do longest[i] = math.min(math.max(longest[i], 6), 60); total = total + longest[i] end
  for i = 1, n do tbl.colspecs[i][2] = 0.98 * longest[i] / total end
  return tbl
end
