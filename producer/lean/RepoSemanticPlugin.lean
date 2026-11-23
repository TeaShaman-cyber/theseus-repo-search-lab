import Lean
import Lean.Util.FoldConsts

open Lean Elab Command Linter

private def declarationName? (stx : Syntax) : Option Name := do
  guard <| stx.isOfKind ``Lean.Parser.Command.declaration
  let declId ← stx.find? (·.isOfKind ``Lean.Parser.Command.declId)
  return declId[0].getId

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

initialize addLinter {
  name := `linter.theseusRepoSemanticPilot
  run := fun stx => do
    let some parsedName := declarationName? stx | return
    let env ← getEnv
    let currNamespace ← getCurrNamespace
    let namespaced := currNamespace.append parsedName
    let fullName :=
      if (env.find? namespaced).isSome then
        namespaced
      else
        parsedName
    let some info := env.find? fullName | return

    let typeDeps := info.type.getUsedConstants
    let valueDeps := match info.value? (allowOpaque := true) with
      | some value => value.getUsedConstants
      | none => #[]

    let fact := Json.mkObj [
      ("schema", Json.str "theseus.lean-semantic-fact.v1"),
      ("module", Json.str (← getMainModule).toString),
      ("full_name", Json.str fullName.toString),
      ("kind", Json.str (declarationKind info)),
      ("type_dependencies", namesJson typeDeps),
      ("value_dependencies", namesJson valueDeps)
    ]
    IO.println s!"THESEUS_FACT\t{Json.compress fact}"
}
