#!/bin/bash
# Atualização automática segura do OpenBot (docker run + volume openbot-data).
# Segue docs/docker.md: API GitHub → backup completo de /data → pull da tag exata → recreate.
# Uso: openbot-update.sh [--dry-run] [--force]
# Pausar: touch /var/lib/openbot-update/PAUSE
set -euo pipefail

IMG_REPO="ghcr.io/nightly-labs/openbot"
CONTAINER="openbot"
VOLUME="openbot-data"
VOL_PATH="/var/lib/docker/volumes/${VOLUME}/_data"
SECCOMP="/opt/openbot-docker/seccomp.json"
BACKUP_ROOT="/root/backups/openbot-auto"
KEEP_BACKUPS=3
STATE_DIR="/var/lib/openbot-update"
PAUSE_FILE="${STATE_DIR}/PAUSE"
LOCK_FILE="/var/lock/openbot-update.lock"
LOG="/var/log/openbot-update.log"
REMI_ID="agent-83ac644a-e2b8-4523-9e88-8180d2bcc1b5"
NET="ai-net"
MEM="5g"
CPUS="4"
SHM="1g"
GH_API="https://api.github.com/repos/nightly-labs/openbot/releases/latest"

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    -h|--help) echo "Usage: $0 [--dry-run] [--force]"; exit 0 ;;
  esac
done

mkdir -p "$STATE_DIR" "$BACKUP_ROOT" /opt/openbot-docker
touch "$LOG"
exec >>"$LOG" 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S %Z'; }
log() { echo "[$(ts)] $*"; }

alert() {
  local text="$1"
  python3 - <<'PY' "$text" || log "ALERTA WhatsApp falhou (não-fatal)"
import json, sys, urllib.request
from urllib.parse import quote
text = sys.argv[1]
sys.path.insert(0, "/opt/ifc-painel/mcp")
import whatsapp_delivery as w
number = w.RECIPIENT.read_text().strip()
if not (number.isdigit() and number.startswith("55")):
    raise SystemExit("recipient_invalid")
key = [l.split(":",1)[1].strip() for l in w.HEADER.read_text().splitlines() if l.lower().startswith("apikey:")][0]
body = json.dumps({"number": number, "text": text, "linkPreview": False}).encode()
req = urllib.request.Request(
    w.BASE + "/message/sendText/" + quote(w.INSTANCE, safe=""),
    data=body,
    headers={"apikey": key, "Content-Type": "application/json"},
    method="POST",
)
urllib.request.urlopen(req, timeout=30).read(2000)
print("alert_sent")
PY
}

norm_ver() {
  local v="$1"
  v="${v#v}"
  echo "$v"
}

ver_gt() {
  # true se $1 > $2 (semver simples X.Y.Z)
  python3 - "$1" "$2" <<'PY'
import sys
def parts(s):
    s=s.strip().lstrip('v')
    out=[]
    for p in s.split('.'):
        try: out.append(int(p))
        except ValueError:
            n=''.join(c for c in p if c.isdigit()) or '0'
            out.append(int(n))
    while len(out)<3: out.append(0)
    return tuple(out[:4])
a,b=parts(sys.argv[1]),parts(sys.argv[2])
sys.exit(0 if a>b else 1)
PY
}

current_image() {
  docker inspect "$CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || true
}

current_version() {
  docker exec "$CONTAINER" openbot version 2>/dev/null | head -1 | tr -d '\r' || true
}

container_hostname() {
  docker inspect "$CONTAINER" --format '{{.Config.Hostname}}' 2>/dev/null || echo openbot
}

health_ok() {
  local expect_ver="${1:-}"
  local running img status remi_ok model_ok
  running=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)
  [[ "$running" == "true" ]] || { log "health: container não está running"; return 1; }
  # espera status online (até ~90s)
  local i
  for i in $(seq 1 18); do
    status=$(docker exec "$CONTAINER" openbot status 2>/dev/null || true)
    echo "$status" | grep -qi online && break
    sleep 5
  done
  echo "$status" | grep -qi online || { log "health: openbot status sem online"; log "$status"; return 1; }
  if [[ -n "$expect_ver" ]]; then
    echo "$status" | grep -q "OpenBot ${expect_ver}" || {
      local got
      got=$(docker exec "$CONTAINER" openbot version 2>/dev/null | head -1 | tr -d '\r' || true)
      [[ "$got" == "$expect_ver" ]] || { log "health: versão esperada $expect_ver, got $got"; return 1; }
    }
  fi
  remi_ok=0
  [[ -f "$VOL_PATH/OpenBot/Agents/$REMI_ID/AGENTS.md" ]] || { log "health: Remi_2 AGENTS.md ausente"; return 1; }
  grep -q "Remi_2" "$VOL_PATH/OpenBot/Agents/$REMI_ID/AGENTS.md" || { log "health: Remi_2 não marcado em AGENTS.md"; return 1; }
  [[ -f "$VOL_PATH/OpenBot/Agents/$REMI_ID/TOOLS.md" ]] || { log "health: Remi_2 TOOLS.md ausente"; return 1; }
  [[ -x "$VOL_PATH/.local/bin/remi-codex-watchdog" || -f "$VOL_PATH/.local/bin/remi-codex-watchdog" ]] || { log "health: remi-codex-watchdog ausente"; return 1; }
  [[ -d "$VOL_PATH/.codex" ]] || { log "health: /data/.codex ausente"; return 1; }
  model_ok=0
  if grep -q "freeai/tudo" "$VOL_PATH/OpenBot/Agents/$REMI_ID/AGENTS.md" \
    && grep -qi "low" "$VOL_PATH/OpenBot/Agents/$REMI_ID/AGENTS.md"; then
    model_ok=1
  fi
  [[ "$model_ok" -eq 1 ]] || { log "health: padrão freeai/tudo + effort low não encontrado em AGENTS.md"; return 1; }
  [[ -f "$VOL_PATH/.config/opencode/opencode.json" ]] || { log "health: opencode.json ausente"; return 1; }
  grep -q '"freeai"' "$VOL_PATH/.config/opencode/opencode.json" || { log "health: provider freeai ausente no opencode.json"; return 1; }
  log "health: OK"
  return 0
}

run_container() {
  local image="$1"
  local hostn="${2:-}"
  if [[ -z "$hostn" ]]; then
    hostn=$(container_hostname)
  fi
  # se o container antigo ainda existir com esse nome, falha — caller remove antes
  docker run -d \
    --name "$CONTAINER" \
    --hostname "$hostn" \
    --network "$NET" \
    --restart unless-stopped \
    --shm-size "$SHM" \
    --memory "$MEM" \
    --cpus "$CPUS" \
    --stop-timeout 60 \
    --security-opt "seccomp=${SECCOMP}" \
    --security-opt no-new-privileges:true \
    -v "${VOLUME}:/data" \
    "$image"
}

backup_data() {
  local stamp="$1"
  local dest="${BACKUP_ROOT}/${stamp}"
  mkdir -p "$dest"
  log "backup: parando container para cópia consistente"
  docker stop -t 60 "$CONTAINER" >/dev/null
  # confirma parado
  local st
  st=$(docker inspect -f '{{.State.Running}}' "$CONTAINER")
  [[ "$st" == "false" ]] || { log "backup: container não parou"; return 1; }
  log "backup: arquivando volume (tar.gz)"
  tar -C "$VOL_PATH" -czf "${dest}/openbot-data.tgz" .
  docker inspect "$CONTAINER" > "${dest}/container-inspect.json"
  echo "$stamp" > "${dest}/stamp.txt"
  # previous-image/version são gravados pelo caller (antes do stop)
  cp -a "$SECCOMP" "${dest}/seccomp.json" 2>/dev/null || true
  # manifesto mínimo
  python3 - <<PY
import hashlib, json, os
dest="${dest}"
path=os.path.join(dest,"openbot-data.tgz")
h=hashlib.sha256()
with open(path,"rb") as f:
  for chunk in iter(lambda: f.read(1024*1024), b""):
    h.update(chunk)
meta={
  "stamp": "${stamp}",
  "sha256": h.hexdigest(),
  "bytes": os.path.getsize(path),
  "volume": "${VOLUME}",
}
open(os.path.join(dest,"manifest.json"),"w").write(json.dumps(meta,indent=2))
print("backup_sha256", meta["sha256"], "bytes", meta["bytes"])
PY
  # retém só os KEEP_BACKUPS mais recentes
  ls -1dt "${BACKUP_ROOT}"/*/ 2>/dev/null | tail -n +$((KEEP_BACKUPS+1)) | while read -r old; do
    log "backup: removendo antigo $old"
    rm -rf "$old"
  done
  log "backup: OK em $dest"
  echo "$dest" > "${STATE_DIR}/last-backup"
}

restore_backup() {
  local dest="$1"
  local prev_img="$2"
  local hostn="${3:-openbot}"
  log "restore: aplicando backup $dest e imagem $prev_img"
  docker stop -t 60 "$CONTAINER" 2>/dev/null || true
  docker rm "$CONTAINER" 2>/dev/null || true
  # limpa volume com cuidado: remove conteúdo, não o volume
  find "$VOL_PATH" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  tar -C "$VOL_PATH" -xzf "${dest}/openbot-data.tgz"
  chown -R 1000:1000 "$VOL_PATH" 2>/dev/null || true
  run_container "$prev_img" "$hostn"
  sleep 8
}

fetch_latest_tag() {
  python3 - <<'PY'
import json, urllib.request, re, sys
req=urllib.request.Request(
  "https://api.github.com/repos/nightly-labs/openbot/releases/latest",
  headers={"Accept":"application/vnd.github+json","User-Agent":"openbot-update-filipe"}
)
try:
  with urllib.request.urlopen(req, timeout=30) as r:
    d=json.loads(r.read().decode())
except Exception as e:
  print("API_ERROR", type(e).__name__, file=sys.stderr)
  sys.exit(2)
tag=d.get("tag_name") or ""
if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
  print("BAD_TAG", tag, file=sys.stderr)
  sys.exit(3)
print(tag)
PY
}

refresh_seccomp_if_needed() {
  local tag="$1"
  local tmp="/tmp/openbot-seccomp-${tag}.json"
  local url="https://raw.githubusercontent.com/nightly-labs/openbot/${tag}/docker/seccomp.json"
  if ! curl -fsS "$url" -o "$tmp"; then
    log "seccomp: falha ao baixar da release $tag — abortando (não usa cópia velha às cegas se a API pediu upgrade)"
    return 1
  fi
  if ! python3 -c "import json; json.load(open('$tmp'))"; then
    log "seccomp: JSON inválido"; return 1
  fi
  if ! cmp -s "$tmp" "$SECCOMP"; then
    log "seccomp: mudou na release — atualizando $SECCOMP"
    cp -a "$SECCOMP" "${SECCOMP}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    cp "$tmp" "$SECCOMP"
    echo "changed"
  else
    log "seccomp: igual ao da release"
    echo "same"
  fi
}

# ---- main ----
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "outro openbot-update em andamento; saindo"
  exit 0
fi

log "==== início (dry_run=$DRY_RUN force=$FORCE) ===="

if [[ -f "$PAUSE_FILE" ]]; then
  log "PAUSE ativo em $PAUSE_FILE — saindo"
  exit 0
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  log "ERRO: container $CONTAINER não existe"
  alert "⚠️ OpenBot update: container ausente na VPS."
  exit 1
fi

CUR_IMG=$(current_image)
CUR_VER=$(norm_ver "$(current_version)")
[[ -n "$CUR_VER" ]] || CUR_VER=$(norm_ver "$(echo "$CUR_IMG" | sed -n 's/.*://p')")
log "atual: image=$CUR_IMG version=$CUR_VER"

LATEST_TAG=$(fetch_latest_tag) || {
  log "ERRO: não foi possível ler releases/latest (sem fallback para :latest)"
  alert "⚠️ OpenBot update: falha ao consultar releases no GitHub. Nenhuma mudança."
  exit 1
}
NEW_VER=$(norm_ver "$LATEST_TAG")
log "latest release: $LATEST_TAG ($NEW_VER)"

if [[ "$FORCE" -ne 1 ]] && ! ver_gt "$NEW_VER" "$CUR_VER"; then
  log "já atualizado (ou candidato não é mais novo). silencioso."
  exit 0
fi

TARGET_IMG="${IMG_REPO}:${NEW_VER}"
log "candidato: $TARGET_IMG"

refresh_seccomp_if_needed "$LATEST_TAG" || {
  alert "⚠️ OpenBot update: falha ao validar seccomp.json da $LATEST_TAG. Abortado."
  exit 1
}
SEC_STATE=checked

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "DRY-RUN: puxaria $TARGET_IMG, faria backup, recrearia container. seccomp=checked"
  # testa se o manifest da imagem existe (sem recrear)
  if docker manifest inspect "$TARGET_IMG" >/dev/null 2>&1; then
    log "DRY-RUN: manifest OK para $TARGET_IMG"
  else
    # fallback: docker pull --quiet em dry-run ainda baixa; preferimos só inspect
    log "DRY-RUN: manifest inspect falhou — tentando pull (pode baixar camadas)"
    docker pull -q "$TARGET_IMG"
  fi
  log "DRY-RUN: health atual:"
  health_ok "$CUR_VER" || log "DRY-RUN: health atual com avisos"
  log "==== dry-run fim ===="
  exit 0
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PREV_IMG="$CUR_IMG"
# tag local de rollback da imagem atual
SAFE_TAG="openbot-preupdate:${CUR_VER}"
docker tag "$CUR_IMG" "$SAFE_TAG" 2>/dev/null || true
log "imagem anterior etiquetada como $SAFE_TAG"

HOSTN=$(container_hostname)
# grava metadados de rollback antes do stop (version some com o container parado)
mkdir -p "${BACKUP_ROOT}/${STAMP}"
echo "$PREV_IMG" > "${BACKUP_ROOT}/${STAMP}/previous-image.txt"
echo "$CUR_VER" > "${BACKUP_ROOT}/${STAMP}/previous-version.txt"
backup_data "$STAMP"
BACKUP_DIR=$(cat "${STATE_DIR}/last-backup")
# container está parado após backup

log "pull: $TARGET_IMG"
if ! docker pull -q "$TARGET_IMG"; then
  log "ERRO: pull falhou — religando imagem anterior"
  run_container "$PREV_IMG" "$HOSTN" || run_container "$SAFE_TAG" "$HOSTN"
  alert "⚠️ OpenBot update: pull de ${NEW_VER} falhou. Mantido ${CUR_VER}."
  exit 1
fi

# confirma label de versão da imagem puxada
PULLED_VER=$(docker image inspect "$TARGET_IMG" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || true)
PULLED_VER=$(norm_ver "${PULLED_VER:-}")
if [[ -n "$PULLED_VER" && "$PULLED_VER" != "$NEW_VER" ]]; then
  log "ERRO: label da imagem ($PULLED_VER) != release ($NEW_VER)"
  run_container "$PREV_IMG" || run_container "$SAFE_TAG"
  alert "⚠️ OpenBot update: imagem ${TARGET_IMG} com versão inesperada. Mantido ${CUR_VER}."
  exit 1
fi

OLD_NAME="${CONTAINER}-${CUR_VER}-old"
docker rm -f "$OLD_NAME" 2>/dev/null || true
docker rename "$CONTAINER" "$OLD_NAME" 2>/dev/null || docker rm -f "$CONTAINER"

log "recreate: $TARGET_IMG"
if ! run_container "$TARGET_IMG" "$HOSTN"; then
  log "ERRO: docker run falhou — restore do backup"
  restore_backup "$BACKUP_DIR" "$SAFE_TAG" "$HOSTN"
  alert "⚠️ OpenBot update: recreate ${NEW_VER} falhou. Restaurado backup + ${CUR_VER}."
  exit 1
fi

sleep 10
if ! health_ok "$NEW_VER"; then
  log "ERRO: health falhou após upgrade — restore backup + imagem anterior (docs: não usar imagem velha em data migrada)"
  restore_backup "$BACKUP_DIR" "$SAFE_TAG" "$HOSTN"
  sleep 10
  if health_ok "$CUR_VER"; then
    alert "⚠️ OpenBot update: ${CUR_VER}→${NEW_VER} falhou no health. Rollback com backup OK. Log: $LOG"
  else
    alert "🚨 OpenBot update: ${NEW_VER} falhou e o rollback também. Intervenção manual. Log: $LOG"
  fi
  # remove container old se existir
  docker rm -f "$OLD_NAME" 2>/dev/null || true
  exit 1
fi

docker rm -f "$OLD_NAME" 2>/dev/null || true
# limpa dangling latest confuso se apontar para antigo — não remove tags versionadas
log "sucesso: ${CUR_VER} → ${NEW_VER}"
echo "${NEW_VER}" > "${STATE_DIR}/last-version"
echo "$(ts)" > "${STATE_DIR}/last-success"
alert "✅ OpenBot atualizado na VPS: ${CUR_VER} → ${NEW_VER}. Backup: ${BACKUP_DIR}. Log: $LOG"
log "==== fim OK ===="
exit 0
