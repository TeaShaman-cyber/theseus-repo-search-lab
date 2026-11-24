import Lean
open Lean

/- A private environment name inventory, not a proof graph. Run in a verified
   pinned dependency workspace. The caller binds the output to that workspace. -/
def main (args : List String) : IO Unit := do
  if args.isEmpty then throw (IO.userError "expected ordered dependency roots")
  let roots := args.toArray.map String.toName
  unless roots.toList.map Name.toString == args do
    throw (IO.userError "unsupported root name")
  Lean.initSearchPath (← Lean.findSysroot)
  let env ← Lean.importModules (roots.map fun n => {module := n}) {} 0
  IO.println <| Json.compress <| Json.mkObj [
    ("schema", Json.str "theseus.lean-dependency-names.v1"),
    ("roots", toJson args),
    ("module_order", toJson (env.header.moduleNames.map Name.toString)),
    ("name_count", toJson env.const2ModIdx.size)]
  for (n, idx) in env.const2ModIdx do
    let some mod := env.header.moduleNames[idx.toNat]? | throw (IO.userError "invalid module index")
    let kind := if (env.find? n).isNone then "ir_only" else if n.isInternalDetail then "internal" else "kernel"
    IO.println <| Json.compress <| toJson [n.toString, mod.toString, kind]
