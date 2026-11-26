import Lean.Elab.ParseImportsFast
import Std.Data.HashSet

open Lean System

def modulePath (sourceRoot : FilePath) (moduleName : String) : FilePath :=
  sourceRoot / (moduleName.replace "." "/" ++ ".lean")

partial def visitProjectModule
    (sourceRoot : FilePath)
    (moduleName : String)
    (seen : Std.HashSet String) : IO (Std.HashSet String) := do
  if seen.contains moduleName then
    return seen

  let path := modulePath sourceRoot moduleName
  unless (← path.pathExists) do
    return seen

  let header ← Lean.parseImports' (← IO.FS.readFile path) path.toString
  let mut next := seen.insert moduleName
  for imp in header.imports do
    next ← visitProjectModule sourceRoot imp.module.toString next
  return next

def main (args : List String) : IO UInt32 := do
  match args with
  | [sourceRootText, rootsFileText] =>
      let sourceRoot := FilePath.mk sourceRootText
      let rootsFile := FilePath.mk rootsFileText
      let roots := (← IO.FS.readFile rootsFile).splitOn "\n" |>.filter (fun s => !s.isEmpty)
      if roots.isEmpty then
        throw <| IO.userError "roots file is empty"

      let mut seen : Std.HashSet String := {}
      for root in roots do
        let path := modulePath sourceRoot root
        unless (← path.pathExists) do
          throw <| IO.userError s!"root module source is missing: {root}"
        seen ← visitProjectModule sourceRoot root seen

      for moduleName in seen.toArray.qsort (· < ·) do
        IO.println moduleName
      return 0
  | _ =>
      IO.eprintln "usage: lean_project_import_closure <source-root> <roots-file>"
      return 2
