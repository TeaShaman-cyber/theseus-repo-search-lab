import Lean

namespace Oracle
theorem D.first : True := by trivial

def D.raw : String := r##"start "
namespace Fake
theorem Ghost.x : True := by trivial
end Fake
finish"##

def D.interpolated : String := s!"{("start
namespace Fake
theorem Ghost.x : True := by trivial
end Fake
finish")}"

def D.quote : Char := '"'

namespace Inner
section Local
theorem D.middle : True := by trivial
end Local
end Inner

theorem D.last : True := by trivial
end Oracle

open Lean in
run_cmd do
  for n in ["Oracle.D.first", "Oracle.D.raw", "Oracle.D.interpolated",
            "Oracle.D.quote", "Oracle.Inner.D.middle", "Oracle.D.last"] do
    let some r ← Lean.findDeclarationRanges? n.toName
      | throwError "missing declaration range: {n}"
    logInfo m!"LEX_ORACLE {n} {r.range.pos.line} {r.range.endPos.line}"
