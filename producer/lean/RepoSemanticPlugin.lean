import Lean
import Lean.Util.FoldConsts

open Lean Elab Command Linter

private def declarationKind : ConstantInfo → String
  | .thmInfo _ => "theorem"
  | .defnInfo _ => "definition"
  | .axiomInfo _ => "axiom"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient"

private def namesJson (names : Array Name) : Json :=
  Json.arr <| names.map fun name => Json.str name.toString

private def writeModuleSidecar (moduleName : Name) (fact : Json) : IO Bool := do
  let some root ← IO.getEnv "THESEUS_REPO_FACT_DIR" | return false
  let root := System.FilePath.mk root
  IO.FS.createDirAll root
  let path := root / s!"{moduleName}.jsonl"
  let h ← IO.FS.Handle.mk path .append
  h.lock (exclusive := true)
  h.putStrLn (Json.compress fact)
  return true

private def emitSemanticFact
    (mainModule fullName : Name)
    (info : ConstantInfo) : CommandElabM Unit := do
  let typeDeps := info.type.getUsedConstants
  let valueDeps := match info.value? (allowOpaque := true) with
    | some value => value.getUsedConstants
    | none => #[]
  let fact := Json.mkObj [
    ("schema", Json.str "theseus.lean-semantic-fact.v1"),
    ("module", Json.str mainModule.toString),
    ("full_name", Json.str fullName.toString),
    ("kind", Json.str (declarationKind info)),
    ("type_dependencies", namesJson typeDeps),
    ("value_dependencies", namesJson valueDeps)
  ]
  let wroteSidecar ← writeModuleSidecar mainModule fact
  unless wroteSidecar do
    IO.println s!"THESEUS_FACT\t{Json.compress fact}"

initialize
  let _ ← registerStatefulLinter (τ := Unit) ({} : PHashMap Name Nat)
    (post := fun _ previous _ _ _ => do
      let env ← getEnv
      let newNames :=
        env.constAddedGens.foldlNewEntries
          (fun names name _ => names.push name)
          previous
          #[]
        |>.qsort Name.quickLt
      let mainModule ← getMainModule
      for fullName in newNames do
        let some info := env.find? fullName
          | throwError "registered constant missing from environment: {fullName}"
        emitSemanticFact mainModule fullName info
      return env.constAddedGens)
  pure ()
