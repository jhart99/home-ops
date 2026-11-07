#!/usr/bin/env bash
#
# image-digest-diff.sh — compare two pinned digests of the same image tag.
#
# Used by .github/workflows/image-digest-diff.yaml on pull requests. Finds
# container image pins where the TAG stayed the same but the DIGEST changed
# (Renovate digest refreshes), fetches both manifests from their registry,
# and renders a markdown report of what actually changed (config env /
# entrypoint / labels / history, layer list, size) for a PR comment.
#
# Handles the pin shapes used in this repo:
#   image: ghcr.io/foo/bar:v1.2.3@sha256:...     (Deployments, MCPServers, ...)
#   imageName: ghcr.io/foo/bar:v1@sha256:...     (CloudNativePG)
#   tag: v1.2.3@sha256:...                       (helm values, paired with repository:)
#   repository: ... / tag: 1.1.2 / digest: sha256:...   (split-field values)
#
# Usage:
#   image-digest-diff.sh --base <sha> [--head <sha>] [options]
#   image-digest-diff.sh --smoke-test
#
# Options:
#   --base <sha>       OLD side revision (required unless --smoke-test)
#   --head <sha>       NEW side revision (default: HEAD)
#   --path <pathspec>  git pathspec to scan (default: kubernetes)
#   --detect-only      print found pairs as TSV and exit (no network, no jq)
#   --comment <file>   PR comment markdown output (default: image-digest-comment.md)
#   --summary <file>   append a run summary here (e.g. $GITHUB_STEP_SUMMARY)
#   --smoke-test       self-test: full pipeline against docker.io alpine
#
set -euo pipefail
export LC_ALL=C

ACCEPT_TYPES="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"
CFG_MAX_LINES=400
LAYERS_MAX_LINES=200
COMMENT_MAX_BYTES=60000

log() { printf '%s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_jq() { command -v jq >/dev/null 2>&1 || die "jq is required for this mode but is not installed"; }
mib() { awk -v b="${1:-0}" 'BEGIN { printf "%.1f MiB", b / 1048576 }'; }
mib_delta() { awk -v a="${1:-0}" -v b="${2:-0}" 'BEGIN { printf "%+.1f MiB", (b - a) / 1048576 }'; }

BASE=""
HEAD_REV="HEAD"
PATHSPEC="kubernetes"
COMMENT_FILE="image-digest-comment.md"
SUMMARY_FILE=""
DETECT_ONLY=0
SMOKE=0

while [ $# -gt 0 ]; do
  case $1 in
    --base) BASE=${2:?}; shift 2 ;;
    --head) HEAD_REV=${2:?}; shift 2 ;;
    --path) PATHSPEC=${2:?}; shift 2 ;;
    --comment) COMMENT_FILE=${2:?}; shift 2 ;;
    --summary) SUMMARY_FILE=${2:?}; shift 2 ;;
    --detect-only) DETECT_ONLY=1; shift ;;
    --smoke-test) SMOKE=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

WORK=$(mktemp -d "${TMPDIR:-/tmp}/imgdiff.XXXXXXXX")
OLD_TSV="$WORK/old.tsv"
NEW_TSV="$WORK/new.tsv"
PAIRS="$WORK/pairs.tsv"
FILEMAP="$WORK/files.tsv"

# ---------------------------------------------------------------------------
# Detection: extract digest-pinned image references from file content.
# Emits one row per reference:  key<TAB>digest<TAB>file
#   key = registry/repo:tag  (everything before @sha256:)
# ---------------------------------------------------------------------------
extract_refs() { # stdin = file content, $1 = file label
  awk -v FL="$1" '
    function emit(k, d) {
      # require a well-formed sha256 digest (7-char prefix + 64 hex chars)
      if (k != "" && d ~ /^sha256:[0-9a-f]+$/ && length(d) == 71)
        printf "%s\t%s\t%s\n", k, d, FL
    }
    function strip_val(s) {
      sub(/^[^:]*:[ \t]*/, "", s)
      gsub(/["\047]/, "", s)
      sub(/[ \t]*,[ \t]*$/, "", s)
      sub(/[ \t]+#.*$/, "", s)
      sub(/^[ \t]+/, "", s)
      sub(/[ \t]+$/, "", s)
      return s
    }
    {
      sub(/\r$/, "")
      s = $0
      sub(/^[ \t]+/, "", s)
      sub(/^-[ \t]+/, "", s)
      if (s ~ /^image(Name)?:/) {
        v = strip_val(s)
        if (index(v, "@sha256:") > 0) {
          at = index(v, "@")
          emit(substr(v, 1, at - 1), substr(v, at + 1))
        }
      } else if (s ~ /^repository:/) {
        repo = strip_val(s); tag = ""
      } else if (s ~ /^tag:/) {
        v = strip_val(s)
        if (index(v, "@sha256:") > 0) {
          at = index(v, "@")
          t = substr(v, 1, at - 1); d = substr(v, at + 1)
          if (repo != "") emit(repo ":" t, d)
          tag = t
        } else {
          tag = v
        }
      } else if (s ~ /^digest:/) {
        v = strip_val(s)
        if (v ~ /^sha256:/ && repo != "" && tag != "")
          emit(repo ":" tag, v)
      }
    }' >> "$2"
}

collect_refs() { # $1=base rev, $2=head rev, $3=pathspec, $4=old out, $5=new out
  local base=$1 head=$2 spec=$3 oldout=$4 newout=$5
  local status a b oldf newf
  : > "$oldout"; : > "$newout"
  while IFS=$'\t' read -r status a b; do
    [ -n "${status:-}" ] || continue
    oldf=""; newf=""
    case $status in
      A) newf=$b ;;
      D) oldf=$a ;;
      R*) oldf=$a; newf=$b ;;
      *) oldf=$a; newf=$a ;;
    esac
    if [ -n "$oldf" ] && git cat-file -e "$base:$oldf" 2>/dev/null; then
      git show "$base:$oldf" 2>/dev/null | extract_refs "$oldf" "$oldout" || true
    fi
    if [ -n "$newf" ] && git cat-file -e "$head:$newf" 2>/dev/null; then
      git show "$head:$newf" 2>/dev/null | extract_refs "$newf" "$newout" || true
    fi
  done < <(git diff --name-status "$base" "$head" -- "$spec" 2>/dev/null || true)
  if [ -s "$oldout" ]; then sort -u "$oldout" -o "$oldout"; fi
  if [ -s "$newout" ]; then sort -u "$newout" -o "$newout"; fi
}

find_pairs() { # $1=old.tsv $2=new.tsv -> $PAIRS (key,oldd,newd) + $FILEMAP (key,files)
  : > "$PAIRS"; : > "$FILEMAP"
  join -t "$(printf '\t')" -j 1 "$1" "$2" > "$WORK/joined.tsv" 2>/dev/null || true
  [ -s "$WORK/joined.tsv" ] || return 0
  awk -F'\t' 'NF >= 5 && $2 != $4 { print $1 "\t" $2 "\t" $4 }' "$WORK/joined.tsv" | sort -u > "$PAIRS"
  # Files where THIS key's digest actually changed (per-file comparison, not the
  # cartesian join output — a key pinned in 7 files must list only changed ones)
  awk -F'\t' '
      NR == FNR { old[$1 SUBSEP $3] = $2; next }
      { new[$1 SUBSEP $3] = $2 }
      END {
        for (kf in old)
          if ((kf in new) && new[kf] != old[kf]) {
            split(kf, p, SUBSEP)
            print p[1] "\t" p[2]
          }
      }' "$1" "$2" | sort -u \
    | awk -F'\t' '
        $1 != prev { if (NR > 1) printf "%s\t%s\n", prev, acc; prev = $1; acc = $2; next }
        { acc = acc ", " $2 }
        END { if (NR > 0) printf "%s\t%s\n", prev, acc }' > "$FILEMAP"
}

files_for_key() { # $1=key -> "file1, file2"
  awk -F'\t' -v k="$1" '$1 == k { print $2 }' "$FILEMAP"
}

# ---------------------------------------------------------------------------
# Registry access (anonymous + bearer challenge, no docker daemon needed)
# ---------------------------------------------------------------------------

token_from_headers() { # $1=header file, $2=repository path (fallback scope)
  local www realm service scope resp tok
  www=$(tr -d '\r' < "$1" | awk 'tolower($1) == "www-authenticate:" { sub(/^[^:]*:[ \t]*/, ""); print; exit }')
  realm=$(printf '%s' "$www" | sed -n 's/.*realm="\([^"]*\)".*/\1/p')
  service=$(printf '%s' "$www" | sed -n 's/.*service="\([^"]*\)".*/\1/p')
  scope=$(printf '%s' "$www" | sed -n 's/.*scope="\([^"]*\)".*/\1/p')
  [ -n "$realm" ] || return 1
  [ -n "$scope" ] || scope="repository:$2:pull"
  local -a q=(-sS --retry 3 --retry-delay 2 -G "$realm" --data-urlencode "scope=$scope")
  if [ -n "$service" ]; then q+=(--data-urlencode "service=$service"); fi
  resp=$(curl "${q[@]}" 2>/dev/null) || return 1
  tok=$(printf '%s' "$resp" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [ -n "$tok" ] || tok=$(printf '%s' "$resp" | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [ -n "$tok" ] || return 1
  printf '%s' "$tok"
}

# fetch_url <url> <outfile> <accept> <repo-path> -> echoes HTTP code, handles one 401 round
fetch_url() {
  local url=$1 out=$2 accept=$3 repo_path=${4:-}
  local hdr="$WORK/hdr.tmp" code tok authz
  local -a args=(-sS -L --retry 3 --retry-delay 2 -D "$hdr" -o "$out" -w '%{http_code}')
  if [ -n "$accept" ]; then args+=(-H "Accept: $accept"); fi
  code=$(curl "${args[@]}" "$url" 2>/dev/null || printf '000')
  if [ "$code" = "401" ]; then
    if tok=$(token_from_headers "$hdr" "$repo_path"); then
      # assemble the auth header at runtime (never a literal credential in source)
      printf -v authz '%s %s %s' "Authorization:" "Bearer" "$tok"
      args+=(-H "$authz")
      code=$(curl "${args[@]}" "$url" 2>/dev/null || printf '000')
    fi
  fi
  printf '%s' "$code"
}

# fetch_manifest <reg> <path> <ref> <outfile> -> echoes content digest ("" if header absent)
fetch_manifest() {
  local reg=$1 path=$2 ref=$3 out=$4
  local code digest
  code=$(fetch_url "https://${reg}/v2/${path}/manifests/${ref}" "$out" "$ACCEPT_TYPES" "$path")
  if [ "$code" != "200" ]; then
    log "WARN: manifest GET https://${reg}/v2/${path}/manifests/${ref} -> HTTP $code"
    return 1
  fi
  digest=$(tr -d '\r' < "$WORK/hdr.tmp" | awk 'tolower($1) == "docker-content-digest:" { print $2; exit }')
  printf '%s' "$digest"
}

is_index() {
  jq -e '((.mediaType // "") | test("index|manifest.list")) or (((.manifests // []) | length) > 0)' \
    "$1" >/dev/null 2>&1
}

# fetch_resolved <reg> <path> <ref> <outfile> -> stdout "digest<TAB>platform-label"
# Resolves a multi-arch index to its linux/amd64 manifest (fetches into <outfile>).
fetch_resolved() {
  local reg=$1 path=$2 ref=$3 out=$4
  local digest label="" pd
  if ! digest=$(fetch_manifest "$reg" "$path" "$ref" "$out"); then return 1; fi
  if is_index "$out"; then
    pd=$(jq -r '[.manifests[]? | select(.platform.os == "linux" and .platform.architecture == "amd64")] | first | .digest // ""' "$out")
    [ -n "$pd" ] || pd=$(jq -r '.manifests[0].digest // ""' "$out")
    if [ -n "$pd" ] && fetch_manifest "$reg" "$path" "$pd" "$out" >/dev/null; then
      label="linux/amd64 (resolved from multi-arch index)"
    fi
  else
    label="single-arch manifest"
  fi
  printf '%s\t%s' "$digest" "$label"
}

fetch_blob() { # <reg> <path> <digest> <outfile>
  local reg=$1 path=$2 dg=$3 out=$4 code
  code=$(fetch_url "https://${reg}/v2/${path}/blobs/${dg}" "$out" "" "$path")
  if [ "$code" != "200" ]; then
    log "WARN: blob GET ${dg} -> HTTP $code"
    return 1
  fi
}

normalize_config() { # <config.json> <outfile>
  jq -S '(.config // {}) as $c | {
    architecture: (.architecture // ""),
    os: (.os // ""),
    created: (.created // ""),
    user: ($c.User // ""),
    Entrypoint: ($c.Entrypoint // []),
    Cmd: ($c.Cmd // []),
    Env: ($c.Env // []),
    Labels: ($c.Labels // {}),
    ExposedPorts: ($c.ExposedPorts // {}),
    WorkingDir: ($c.WorkingDir // ""),
    Healthcheck: ($c.Healthcheck // null),
    StopSignal: ($c.StopSignal // ""),
    history: [(.history // [])[] | {
      created_by: (.created_by // ""),
      comment: (.comment // ""),
      empty_layer: (.empty_layer // false)
    }]
  }' "$1" > "$2"
}

# analyze_side <reg> <path> <manifest-file> <side> <ref> <platform-label>
# Writes $WORK/<side>.cfgnorm and $WORK/<side>.layers; echoes the meta line.
analyze_side() {
  local reg=$1 path=$2 mf=$3 side=$4 ref=$5 plat=$6
  local cfgdg sum n created="" meta cfgok="no"
  sum=$(jq -r '[.layers[]?.size // 0] | add // 0' "$mf")
  n=$(jq -r '(.layers // []) | length' "$mf")
  jq -r '.layers[]? | [(.size // 0), (.digest // "")] | @tsv' "$mf" > "$WORK/$side.layers"
  cfgdg=$(jq -r '.config.digest // ""' "$mf" 2>/dev/null || printf '')
  if [ -n "$cfgdg" ] && fetch_blob "$reg" "$path" "$cfgdg" "$WORK/$side.cfg"; then
    normalize_config "$WORK/$side.cfg" "$WORK/$side.cfgnorm"
    created=$(jq -r '.created // "unknown"' "$WORK/$side.cfg")
    cfgok="yes"
  fi
  meta="${plat:-unknown platform}; created ${created:-config unavailable}; ${n} layers; $(mib "$sum")"
  printf '%s' "$meta"
}

# render_pair <key> <old-ref> <new-ref> <files>  (appends section to $COMMENT_FILE)
render_pair() {
  local key=$1 oldref=$2 newref=$3 files=$4
  local repo=${key%:*} tag=${key##*:}
  local filelist
  filelist=$(printf '%s' "$files" | sed 's/, /`, `/g; s/^/`/; s/$/`/')

  {
    printf '### `%s`\n\n' "$key"
    printf '`%s` → `%s`\n\n' "$oldref" "$newref"
    printf -- '- Pin updated in: %s\n' "$filelist"
  } >> "$COMMENT_FILE"

  if [[ "$repo" == *'$'* ]]; then
    printf -- '- Registry path contains substituted variables — fetch skipped.\n\n' >> "$COMMENT_FILE"
    return 0
  fi

  local reg path
  reg=${repo%%/*}
  path=${repo#*/}
  if [[ $reg != *.* && $reg != *:* && $reg != "localhost" ]]; then
    path=$repo
    reg="registry-1.docker.io"
  fi
  if [ "$reg" = "docker.io" ]; then reg="registry-1.docker.io"; fi
  if [ "$reg" = "registry-1.docker.io" ] && [[ $path != */* ]]; then path="library/$path"; fi

  local oldmf="$WORK/pair.old.mf" newmf="$WORK/pair.new.mf"
  local oldinfo="" newinfo=""
  local old_ok=0 new_ok=0
  if oldinfo=$(fetch_resolved "$reg" "$path" "$oldref" "$oldmf"); then old_ok=1; fi
  if newinfo=$(fetch_resolved "$reg" "$path" "$newref" "$newmf"); then new_ok=1; fi

  if [ "$old_ok" -ne 1 ] && [ "$new_ok" -ne 1 ]; then
    printf -- '- ⚠️ Neither manifest could be fetched from `%s` (registry unreachable or digest gone).\n\n' "$reg" >> "$COMMENT_FILE"
    return 0
  fi

  local oldplat="" newplat=""
  if [ "$old_ok" -eq 1 ]; then oldplat=${oldinfo#*$'\t'}; fi
  if [ "$new_ok" -eq 1 ]; then newplat=${newinfo#*$'\t'}; fi

  local meta_old="manifest fetch failed" meta_new="manifest fetch failed"
  if [ "$old_ok" -eq 1 ]; then
    meta_old=$(analyze_side "$reg" "$path" "$oldmf" "old" "$oldref" "$oldplat")
  fi
  if [ "$new_ok" -eq 1 ]; then
    meta_new=$(analyze_side "$reg" "$path" "$newmf" "new" "$newref" "$newplat")
  fi

  printf -- '- old: %s\n' "$meta_old" >> "$COMMENT_FILE"
  printf -- '- new: %s\n' "$meta_new" >> "$COMMENT_FILE"

  if [ "$old_ok" -eq 1 ] && [ "$new_ok" -eq 1 ]; then
    local osz nsz
    osz=$(jq -r '[.layers[]?.size // 0] | add // 0' "$oldmf")
    nsz=$(jq -r '[.layers[]?.size // 0] | add // 0' "$newmf")
    printf -- '- size delta: %s\n' "$(mib_delta "$osz" "$nsz")" >> "$COMMENT_FILE"
  fi
  printf '\n' >> "$COMMENT_FILE"

  if [ "$old_ok" -ne 1 ] || [ "$new_ok" -ne 1 ]; then
    return 0
  fi

  local diff_out lines
  # Layer list diff
  if diff_out=$(diff -u --label "layers (old)" --label "layers (new)" "$WORK/old.layers" "$WORK/new.layers" 2>/dev/null); then
    printf -- 'Layer list is byte-identical between the two manifests.\n\n' >> "$COMMENT_FILE"
  else
    lines=$(printf '%s\n' "$diff_out" | wc -l)
    {
      printf '<details><summary>Layer changes (%s → %s layers)</summary>\n\n' \
        "$(jq -r '(.layers // []) | length' "$oldmf")" "$(jq -r '(.layers // []) | length' "$newmf")"
      printf '```diff\n'
      if [ "$lines" -gt "$LAYERS_MAX_LINES" ]; then
        printf '%s\n' "$diff_out" | head -n "$LAYERS_MAX_LINES"
        printf '... (truncated, full diff in workflow summary)\n'
      else
        printf '%s\n' "$diff_out"
      fi
      printf '```\n\n</details>\n\n'
    } >> "$COMMENT_FILE"
  fi

  # Config diff
  if [ -s "$WORK/old.cfgnorm" ] && [ -s "$WORK/new.cfgnorm" ]; then
    if diff_out=$(diff -u --label "config (old)" --label "config (new)" "$WORK/old.cfgnorm" "$WORK/new.cfgnorm" 2>/dev/null); then
      printf 'Image config is identical (same env, entrypoint, cmd, labels and history).\n\n' >> "$COMMENT_FILE"
    else
      lines=$(printf '%s\n' "$diff_out" | wc -l)
      {
        printf '<details><summary>Image config changes (env, entrypoint, cmd, labels, history)</summary>\n\n'
        printf '```diff\n'
        if [ "$lines" -gt "$CFG_MAX_LINES" ]; then
          printf '%s\n' "$diff_out" | head -n "$CFG_MAX_LINES"
          printf '... (truncated, full diff in workflow summary)\n'
        else
          printf '%s\n' "$diff_out"
        fi
        printf '```\n\n</details>\n\n'
      } >> "$COMMENT_FILE"
    fi
  else
    printf 'Config blob could not be fetched — showing manifest/layer changes only.\n\n' >> "$COMMENT_FILE"
  fi
}

# ---------------------------------------------------------------------------
# Smoke test: full pipeline (fetch + resolve + normalize + diff + render)
# against two known docker.io tags. Fails the job if any stage is broken.
# ---------------------------------------------------------------------------
smoke_test() {
  require_jq
  # Leg 1: docker.io — challenge auth, multi-arch resolve, config+layer diff, render
  COMMENT_FILE="$WORK/smoke.md"
  : > "$COMMENT_FILE"
  log "smoke: rendering alpine edge -> latest (docker.io) ..."
  render_pair "docker.io/library/alpine:smoke-test" "edge" "latest" "self-test"
  [ -s "$COMMENT_FILE" ] || die "smoke: empty rendered output"
  grep -q 'sha256:' "$COMMENT_FILE" || die "smoke: rendered output has no digests"
  grep -q 'edge' "$COMMENT_FILE" || die "smoke: rendered output missing old ref"
  if ! grep -q '```diff' "$COMMENT_FILE" && ! grep -q 'identical' "$COMMENT_FILE"; then
    die "smoke: output has neither a diff block nor an identical verdict"
  fi
  # Leg 2: ghcr.io — anonymous challenge + manifest + config blob (most pins live here)
  log "smoke: fetching ghcr.io/home-operations/busybox:1.38.0 ..."
  local ginfo cfgdg
  if ! ginfo=$(fetch_resolved "ghcr.io" "home-operations/busybox" "1.38.0" "$WORK/smoke_ghcr.mf"); then
    die "smoke: ghcr.io manifest fetch failed"
  fi
  cfgdg=$(jq -r '.config.digest // ""' "$WORK/smoke_ghcr.mf")
  [ -n "$cfgdg" ] || die "smoke: ghcr manifest has no config digest"
  if ! fetch_blob "ghcr.io" "home-operations/busybox" "$cfgdg" "$WORK/smoke_ghcr.cfg"; then
    die "smoke: ghcr config blob fetch failed"
  fi
  jq -e '.architecture' "$WORK/smoke_ghcr.cfg" >/dev/null || die "smoke: ghcr config unreadable"
  log "smoke: OK (docker.io + ghcr.io)"
  printf '\n--- smoke test rendered section ---\n'
  cat "$COMMENT_FILE"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  if [ "$SMOKE" -eq 1 ]; then
    smoke_test
    return 0
  fi
  [ -n "$BASE" ] || die "--base <sha> is required"

  collect_refs "$BASE" "$HEAD_REV" "$PATHSPEC" "$OLD_TSV" "$NEW_TSV"
  find_pairs "$OLD_TSV" "$NEW_TSV"

  if [ "$DETECT_ONLY" -eq 1 ]; then
    printf 'old refs: %s\n' "$(wc -l < "$OLD_TSV")"
    printf 'new refs: %s\n' "$(wc -l < "$NEW_TSV")"
    printf 'pairs:\n'
    cat "$PAIRS"
    printf 'files:\n'
    cat "$FILEMAP"
    if [ -n "${IMGDIFF_SHOW_REFS:-}" ]; then
      printf 'old rows:\n'; cat "$OLD_TSV"
      printf 'new rows:\n'; cat "$NEW_TSV"
    fi
    return 0
  fi

  require_jq
  : > "$COMMENT_FILE"
  if [ -n "$SUMMARY_FILE" ]; then
    printf '## Container digest diff (%s..%s)\n\n' "$BASE" "$HEAD_REV" > "$SUMMARY_FILE"
  fi

  local count=0
  local key oldd newd files
  if [ -s "$PAIRS" ]; then
    printf '## Container digest changes\n\n' >> "$COMMENT_FILE"
    printf 'The same tag was re-pinned to a new digest for the image(s) below. Diff shows the **linux/amd64** image config and layers.\n\n' >> "$COMMENT_FILE"
    while IFS=$'\t' read -r key oldd newd; do
      [ -n "$key" ] || continue
      files=$(files_for_key "$key")
      [ -n "$files" ] || files="unknown file"
      render_pair "$key" "$oldd" "$newd" "$files"
      count=$((count + 1))
    done < "$PAIRS"
    printf '%s image(s) with digest-only changes.\n' "$count" >&2
  else
    printf 'No digest-only image changes detected (tags changed, files added/removed, or no pinned images touched).\n' >&2
    if [ -n "$SUMMARY_FILE" ]; then
      printf 'No digest-only image changes detected (tags changed, files added/removed, or no pinned images touched).\n' >> "$SUMMARY_FILE"
    fi
  fi

  # Full content goes to the workflow summary BEFORE the comment is truncated
  if [ -n "$SUMMARY_FILE" ] && [ -s "$COMMENT_FILE" ]; then
    cat "$COMMENT_FILE" >> "$SUMMARY_FILE"
  fi

  # Comment size guard (GitHub limit is 65536 chars)
  local size
  size=$(wc -c < "$COMMENT_FILE")
  if [ "$size" -gt "$COMMENT_MAX_BYTES" ]; then
    log "comment is $size bytes — truncating at $COMMENT_MAX_BYTES"
    awk -v max="$COMMENT_MAX_BYTES" '
      { if (total + length($0) + 1 > max) {
          print ""
          print "> Comment truncated — the full diff is in the workflow summary."
          exit
        }
        print
        total += length($0) + 1 }' "$COMMENT_FILE" > "$WORK/trunc"
    cp "$WORK/trunc" "$COMMENT_FILE"
  fi
}

main
