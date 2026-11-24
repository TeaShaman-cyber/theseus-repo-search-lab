import Lean
import Lean.Util.FoldConsts
open Lean

/-
Producer research component, pinned to Lean 4.36.0. Read only trusted, verified
module artifacts. The caller binds `mod` to the source/cache manifest and the
file set; this reader does not establish provenance or canonical ownership.
One native process per module keeps compacted-region lifetime process-local.
The private part contains the final kernel constant set for split modules.
No import closure is loaded; raw fact occurrences are preserved.
-/
private def kind : ConstantInfo → String
  | .thmInfo _ => "theorem"
  | .defnInfo _ => "definition"
  | .axiomInfo _ => "axiom"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient"
private def readData (base : System.FilePath) : IO ModuleData := do
  if ← (base.addExtension "private").pathExists then do
    let parts ← readModuleDataParts #[base, base.addExtension "server", base.addExtension "private"]
    let some part := parts[2]? | throw (IO.userError "missing private part")
    pure part.1
  else do
    let (data, _) ← readModuleData base
    if data.isModule then throw (IO.userError "split module missing parts")
    pure data

private def factJson (mod : String) (info : ConstantInfo) : Json :=
  let deps := match info.value? (allowOpaque := true) with
    | some value => value.getUsedConstants
    | none => #[]
  Json.mkObj [
    ("schema", Json.str "theseus.lean-semantic-fact.v1"),
    ("module", Json.str mod), ("full_name", Json.str info.name.toString),
    ("kind", Json.str (kind info)),
    ("type_dependencies", Json.arr (info.type.getUsedConstants.map (fun n => Json.str n.toString))),
    ("value_dependencies", Json.arr (deps.map (fun n => Json.str n.toString)))]

private def findTheorem (data : ModuleData) (target : String) : IO TheoremVal := do
  let candidates := data.constants.filter (fun ci => ci.name.toString == target)
  let #[.thmInfo thm] := candidates | throw (IO.userError "expected exactly one matching theorem")
  if thm.name.isInternalDetail then throw (IO.userError "internal theorem unsupported")
  pure thm

-- The theorem/theorem branch of Lean 4.36 finalizeImport/subsumesInfo.
-- No claim about proof equality, theorem/axiom subsumption, or import order.
-- Caller provides verified file sets in actual traversal order.
private def resolveTheorem (target aMod aPath bMod bPath : String) : IO Unit := do
  let a ← findTheorem (← readData (System.FilePath.mk aPath)) target
  let b ← findTheorem (← readData (System.FilePath.mk bPath)) target
  unless a.name == b.name && a.type == b.type && a.levelParams == b.levelParams && a.all == b.all do
    throw (IO.userError "theorem subsumption not established")
  IO.println <| Json.compress <| Json.mkObj [
    ("schema", Json.str "theseus.lean-theorem-resolution.v1"),
    ("previous", factJson aMod (.thmInfo a)), ("selected", factJson bMod (.thmInfo b))]

def main (args : List String) : IO Unit := do
  if let ["--resolve-theorem", target, aMod, aPath, bMod, bPath] := args then
    return ← resolveTheorem target aMod aPath bMod bPath
  let (mod, path, metadataPath) ← match args with
    | [mod, path] => pure (mod, path, none)
    | [mod, path, "--metadata", metaFile] => pure (mod, path, some (System.FilePath.mk metaFile))
    | _ => throw (IO.userError "expected module and path, optionally --metadata PATH")
  let base := System.FilePath.mk path
  let data ← readData base
  let mut factCount := 0
  for info in data.constants do
    unless info.name.isInternalDetail do
      factCount := factCount + 1
      IO.println <| Json.compress <| factJson mod info
  if let some metaFile := metadataPath then
    -- Preserve serialized order. This is .olean metadata, not the final
    -- imported Environment's ownership map or a complete IR inventory.
    let imports := data.imports.map fun imp => Json.mkObj [
      ("module", Json.str imp.module.toString),
      ("is_exported", Json.bool imp.isExported),
      ("import_all", Json.bool imp.importAll),
      ("is_meta", Json.bool imp.isMeta)]
    let irSig := base.withExtension "ir.sig"
    let ir := base.withExtension "ir"
    let irNames ← if (← irSig.pathExists) && (← ir.pathExists) then do
      let parts ← readModuleDataParts #[irSig, ir]
      let some part := parts[1]? | throw (IO.userError "missing IR part")
      pure (Json.arr (part.1.extraConstNames.map (fun n => Json.str n.toString)))
    else pure Json.null
    IO.FS.writeFile metaFile (Json.compress (Json.mkObj [
      ("schema", Json.str "theseus.lean-module-metadata.v1"),
      ("module", Json.str mod),
      ("is_module", Json.bool data.isModule),
      ("imports", Json.arr imports),
      ("constant_names", Json.arr (data.constNames.map (fun n => Json.str n.toString))),
      ("olean_extra_const_names", Json.arr (data.extraConstNames.map (fun n => Json.str n.toString))),
      ("ir_extra_const_names", irNames),
      ("fact_count", toJson factCount)]) ++ "\n")
