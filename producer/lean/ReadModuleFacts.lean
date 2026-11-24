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
def main (args : List String) : IO Unit := do
  let (mod, path, metadataPath) ← match args with
    | [mod, path] => pure (mod, path, none)
    | [mod, path, "--metadata", metaFile] => pure (mod, path, some (System.FilePath.mk metaFile))
    | _ => throw (IO.userError "expected module and path, optionally --metadata PATH")
  let base := System.FilePath.mk path
  let data ← if ← (base.addExtension "private").pathExists then do
    let parts ← readModuleDataParts #[base, base.addExtension "server", base.addExtension "private"]
    let some part := parts[2]? | throw (IO.userError "missing private part")
    pure part.1
  else do
    let (data, _) ← readModuleData base
    if data.isModule then throw (IO.userError "split module missing parts")
    pure data
  let mut factCount := 0
  for info in data.constants do
    unless info.name.isInternalDetail do
      factCount := factCount + 1
      let deps := match info.value? (allowOpaque := true) with
        | some value => value.getUsedConstants
        | none => #[]
      IO.println <| Json.compress <| Json.mkObj [
        ("schema", Json.str "theseus.lean-semantic-fact.v1"),
        ("module", Json.str mod), ("full_name", Json.str info.name.toString),
        ("kind", Json.str (kind info)),
        ("type_dependencies", Json.arr (info.type.getUsedConstants.map (fun n => Json.str n.toString))),
        ("value_dependencies", Json.arr (deps.map (fun n => Json.str n.toString)))]
  if let some metaFile := metadataPath then
    -- Preserve serialized order. This is .olean metadata, not the final
    -- imported Environment's ownership map or a complete IR inventory.
    let imports := data.imports.map fun imp => Json.mkObj [
      ("module", Json.str imp.module.toString),
      ("is_exported", Json.bool imp.isExported),
      ("import_all", Json.bool imp.importAll),
      ("is_meta", Json.bool imp.isMeta)]
    IO.FS.writeFile metaFile (Json.compress (Json.mkObj [
      ("schema", Json.str "theseus.lean-module-metadata.v1"),
      ("module", Json.str mod),
      ("is_module", Json.bool data.isModule),
      ("imports", Json.arr imports),
      ("constant_names", Json.arr (data.constNames.map (fun n => Json.str n.toString))),
      ("olean_extra_const_names", Json.arr (data.extraConstNames.map (fun n => Json.str n.toString))),
      ("fact_count", toJson factCount)]) ++ "\n")
