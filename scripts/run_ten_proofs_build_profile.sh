#!/usr/bin/env bash
set -euo pipefail

profile=${BUILD_PROFILE:?BUILD_PROFILE is required}
source_root=${SOURCE_ROOT:?SOURCE_ROOT is required}
telemetry_interval=${TELEMETRY_INTERVAL_SECONDS:-60}
result_dir=${BUILD_PROFILE_RESULT_DIR:-$GITHUB_WORKSPACE/_build-profile}
mkdir -p "$result_dir"
telemetry_file="$result_dir/${profile}-telemetry.log"
stages_file="$result_dir/${profile}-stages.tsv"
: >"$telemetry_file"
: >"$stages_file"

sphere=0
long_roots=0
threads=0
memory=0
case "$profile" in
  all-in) sphere=1; long_roots=1; threads=1; memory=1 ;;
  sphere-prestage) sphere=1 ;;
  long-roots-prestage) long_roots=1 ;;
  lean-j2) threads=1 ;;
  lean-memory-12288) memory=1 ;;
  *) echo "unknown BUILD_PROFILE=$profile" >&2; exit 2 ;;
esac

lake_args=()
if (( threads )); then
  lake_args+=("-KweakLeanArgs=-j2")
fi
if (( memory )); then
  lake_args+=("-KmoreLeanArgs=-M12288")
fi

printf 'build_profile=%s sphere_prestage=%s long_roots_prestage=%s lean_j2=%s lean_memory_12288=%s\n' \
  "$profile" "$sphere" "$long_roots" "$threads" "$memory"
printf 'lake_config_args='; printf '%q ' "${lake_args[@]}"; printf '\n'

started_all=$(date +%s)
heartbeat() {
  while true; do
    sleep "$telemetry_interval"
    now=$(date +%s)
    line=$(python3 scripts/ci_telemetry.py snapshot \
      --phase "build-${profile}" \
      --elapsed-seconds "$((now - started_all))" \
      --disk-path /)
    printf '%s\n' "$line" | tee -a "$telemetry_file"
  done
}
heartbeat & heartbeat_pid=$!
cleanup() {
  kill "$heartbeat_pid" 2>/dev/null || true
  wait "$heartbeat_pid" 2>/dev/null || true
}
on_term() {
  now=$(date +%s)
  python3 scripts/ci_telemetry.py snapshot \
    --phase "build-${profile}-term" \
    --elapsed-seconds "$((now - started_all))" \
    --disk-path / | tee -a "$telemetry_file" || true
  cleanup
  exit 143
}
trap cleanup EXIT INT
trap on_term TERM

run_stage() {
  local name=$1
  shift
  local started finished rc
  started=$(date +%s)
  printf 'profile_stage_begin profile=%s stage=%s ts=%s\n' "$profile" "$name" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  set +e
  /usr/bin/time -v -o "$result_dir/${profile}-${name}.time" \
    python3 scripts/producer_guard.py run --cwd "$source_root" -- \
      lake "${lake_args[@]}" build "$@"
  rc=$?
  set -e
  finished=$(date +%s)
  cat "$result_dir/${profile}-${name}.time" || true
  printf '%s\t%s\t%s\n' "$name" "$((finished - started))" "$rc" >>"$stages_file"
  printf 'profile_stage_end profile=%s stage=%s seconds=%s rc=%s ts=%s\n' \
    "$profile" "$name" "$((finished - started))" "$rc" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  return "$rc"
}

if (( long_roots )); then
  for root in GapCVP ConnesRigidity QuantumParallelRepetition MetricCodes; do
    run_stage "prestage-${root}" "$root"
  done
fi
if (( sphere )); then
  run_stage prestage-SpherePacking SpherePacking
fi
run_stage final-All "${BUILD_TARGET:-All}"

cleanup
trap - EXIT INT TERM
finished_all=$(date +%s)
printf 'profile_total_seconds=%s\n' "$((finished_all - started_all))"
printf 'source_build_kib=%s\n' "$(du -sk "$source_root/.lake/build" | awk '{print $1}')"
python3 - "$result_dir" "$profile" "$((finished_all - started_all))" "$sphere" "$long_roots" "$threads" "$memory" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
profile = sys.argv[2]
payload = {
    "schema": "theseus.ten-proofs-build-profile.v1",
    "profile": profile,
    "total_seconds": int(sys.argv[3]),
    "optimizations": {
        "sphere_prestage": bool(int(sys.argv[4])),
        "long_roots_prestage": bool(int(sys.argv[5])),
        "lean_j2": bool(int(sys.argv[6])),
        "lean_memory_12288": bool(int(sys.argv[7])),
    },
}
stages = []
for line in (out / f"{profile}-stages.tsv").read_text(encoding="utf-8").splitlines():
    name, seconds, rc = line.split("\t")
    stages.append({"name": name, "seconds": int(seconds), "rc": int(rc)})
payload["stages"] = stages
(out / f"{profile}-receipt.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
