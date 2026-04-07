#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# run_doctor.sh — Launcher do Sub-Agente Doctor
# Roda diretamente na VPS como root (sem Docker próprio)
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export DOCTOR_DIR="${DOCTOR_DIR:-/root/doctor_ribeiro}"
export DOCTOR_REPO_DIR="${DOCTOR_REPO_DIR:-/root/.openclaw/workspace/projetos/assistente_secretario_ribeiro}"
export DOCTOR_CODEBASE_DIR="${DOCTOR_CODEBASE_DIR:-${DOCTOR_REPO_DIR}/codigo}"
export DOCTOR_COMPOSE_FILE="${DOCTOR_COMPOSE_FILE:-${DOCTOR_CODEBASE_DIR}/docker-compose.yml}"
export DOCTOR_MONITORED_SERVICES="${DOCTOR_MONITORED_SERVICES:-postgres,redis,api,celery-worker,celery-beat}"
export DOCTOR_OPTIONAL_SERVICES="${DOCTOR_OPTIONAL_SERVICES:-}"
export DOCTOR_REPORT_DIR="${DOCTOR_REPORT_DIR:-${DOCTOR_DIR}/reports}"
export DOCTOR_STATE_DIR="${DOCTOR_STATE_DIR:-${DOCTOR_DIR}/state}"
export DOCTOR_LOG_DIR="${DOCTOR_LOG_DIR:-${DOCTOR_DIR}/logs}"
export DOCTOR_LOG_FILE="${DOCTOR_LOG_FILE:-${DOCTOR_LOG_DIR}/doctor.log}"
export DOCTOR_PENDING_NOTIFICATION_FILE="${DOCTOR_PENDING_NOTIFICATION_FILE:-${DOCTOR_STATE_DIR}/pending_notification.json}"
export AISHA_NOTIFY_ENDPOINT="${AISHA_NOTIFY_ENDPOINT:-}"
export TZ="${TZ:-America/Sao_Paulo}"
export OPENCLAW_ENV_FILE="${OPENCLAW_ENV_FILE:-/opt/openclaw/.env}"
export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/root/.openclaw/openclaw.json}"
export DOCTOR_AI_PRIMARY_PROVIDER="${DOCTOR_AI_PRIMARY_PROVIDER:-ollama}"

# IA Providers (cadeia de fallback: Claude → Gemini → Ollama)
# O doctor também tenta reaproveitar o contrato do OpenClaw via OPENCLAW_ENV_FILE / OPENCLAW_CONFIG_PATH.
# Descomente e preencha apenas se quiser sobrescrever:
# export ANTHROPIC_API_KEY="sk-ant-..."
# export GEMINI_API_KEY="AIza..."
# export OLLAMA_URL="http://localhost:11434"
# export OLLAMA_BASE_URL="https://ollama.com/v1"
# export OLLAMA_API_KEY="..."
# export OLLAMA_MODEL="qwen2.5:7b"

mkdir -p "${DOCTOR_DIR}" "${DOCTOR_REPORT_DIR}" "${DOCTOR_STATE_DIR}" "${DOCTOR_LOG_DIR}"

DOCTOR_MAIN="${DOCTOR_MAIN:-${DOCTOR_DIR}/doctor.py}"
if [ ! -f "${DOCTOR_MAIN}" ] && [ -f "${SCRIPT_DIR}/doctor.py" ]; then
    DOCTOR_MAIN="${SCRIPT_DIR}/doctor.py"
fi

LOG_FILE="${DOCTOR_LOG_FILE}"

echo "" >> "${LOG_FILE}"
echo "=========================================" >> "${LOG_FILE}"
echo "[$(date)] Doctor iniciando..." >> "${LOG_FILE}"

python3 "${DOCTOR_MAIN}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

echo "[$(date)] Doctor finalizado (exit: ${EXIT_CODE})" >> "${LOG_FILE}"

# Rotação simples do log (manter últimas 5000 linhas)
if [ -f "${LOG_FILE}" ] && [ "$(wc -l < "${LOG_FILE}")" -gt 5000 ]; then
    tail -3000 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
fi
