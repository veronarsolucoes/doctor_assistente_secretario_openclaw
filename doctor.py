#!/usr/bin/env python3
"""
doctor.py — Sub-Agente Doctor para o Assistente Verona
Roda diretamente na VPS (sem Docker próprio).
Monitora containers Docker do sistema, audita segurança, autocorrige e reporta.

Ativação: CRON diário às 04:50 (horário de Brasília)
Autor: Hely
"""

import os
import re
import json
import hashlib
import subprocess
import shlex
import shutil
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta

# Importar cérebro de IA
sys.path.insert(0, str(Path(__file__).parent))
from doctor_brain import DoctorBrain

# ══════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO — ajuste conforme seu ambiente
# ══════════════════════════════════════════════════════════════════

DEFAULT_REPO_DIR = "/root/.openclaw/workspace/projetos/assistente_secretario_ribeiro"

REPO_DIR = Path(os.getenv("DOCTOR_REPO_DIR", DEFAULT_REPO_DIR))
REPO_CODE_DIR = Path(os.getenv("DOCTOR_CODEBASE_DIR", str(REPO_DIR / "codigo")))
DOCKER_COMPOSE_FILE = Path(os.getenv("DOCTOR_COMPOSE_FILE", str(REPO_CODE_DIR / "docker-compose.yml")))
DOCKER_COMPOSE_CMD = os.getenv("DOCTOR_DOCKER_COMPOSE_CMD", "docker compose")
DOCTOR_DIR = Path(os.getenv("DOCTOR_DIR", "/root/doctor_ribeiro"))
REPORT_DIR = Path(os.getenv("DOCTOR_REPORT_DIR", str(DOCTOR_DIR / "reports")))
STATE_DIR = Path(os.getenv("DOCTOR_STATE_DIR", str(DOCTOR_DIR / "state")))
DOCTOR_LOG_DIR = Path(os.getenv("DOCTOR_LOG_DIR", str(DOCTOR_DIR / "logs")))
MEMORY_FILE = Path(os.getenv("DOCTOR_MEMORY_FILE", str(STATE_DIR / "doctor_memory.json")))
SHARED_MEMORY_FILE = Path(os.getenv("DOCTOR_SHARED_MEMORY_FILE", str(STATE_DIR / "shared_memory.json")))
PENDING_NOTIFICATION_FILE = Path(
    os.getenv("DOCTOR_PENDING_NOTIFICATION_FILE", str(STATE_DIR / "pending_notification.json"))
)
API_BASE_URL = os.getenv("DOCTOR_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
LOG_DIRS = [
    REPO_CODE_DIR / "logs",
    REPO_DIR / "logs",
    DOCTOR_LOG_DIR,
    Path("/var/log"),
]
CREDENTIALS_DIR = REPO_CODE_DIR / "credentials"
CREDENTIALS_GITKEEP = CREDENTIALS_DIR / ".gitkeep"
REPO_GITIGNORE = REPO_DIR / ".gitignore"
CREDENTIALS_IGNORE_RULES = (
    "codigo/credentials/*",
    "!codigo/credentials/.gitkeep",
)
AISHA_NOTIFY_ENDPOINT = os.getenv("AISHA_NOTIFY_ENDPOINT", "")
BENIGN_ACCESS_PATTERNS = (
    "hub.verify_token=invalid-token",
    "get /api/v1/webhooks/whatsapp",
)

def parse_csv_env(name, default):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Servicos Docker Compose do projeto monitorados pelo Doctor
MONITORED_SERVICES = parse_csv_env(
    "DOCTOR_MONITORED_SERVICES",
    "postgres,redis,api,celery-worker,celery-beat",
)
OPTIONAL_SERVICES = set(parse_csv_env("DOCTOR_OPTIONAL_SERVICES", ""))

# Arquivos críticos para hash (relativos ao REPO_DIR)
CRITICAL_FILE_PATTERNS = ["*.py", "*.js", "*.env", "docker-compose*.yml", "*.json"]
CRITICAL_DIRS = ["codigo", "config", "."]

# Severidades
CRITICA = "CRÍTICA"
ALTA = "ALTA"
MEDIA = "MÉDIA"
BAIXA = "BAIXA"

# Tipos
APLICACAO = "APLICAÇÃO"
INFRA = "INFRAESTRUTURA"
SEGURANCA = "SEGURANÇA"
DADOS = "DADOS"

NOW = datetime.now()
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
ISO_NOW = NOW.isoformat()


# ══════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ══════════════════════════════════════════════════════════════════

def run(cmd, timeout=60, check=False):
    """Executa comando shell e retorna (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def compose_cmd(args):
    workdir = REPO_CODE_DIR if REPO_CODE_DIR.exists() else REPO_DIR
    return (
        f"cd {shlex.quote(str(workdir))} && "
        f"{DOCKER_COMPOSE_CMD} -f {shlex.quote(str(DOCKER_COMPOSE_FILE))} {args}"
    )


_SERVICE_CONTAINER_CACHE = {}
_SERVICE_LOG_SINCE_CACHE = {}


def get_service_container_id(service):
    if service in _SERVICE_CONTAINER_CACHE:
        return _SERVICE_CONTAINER_CACHE[service]

    rc, out, _ = run(compose_cmd(f"ps -q {shlex.quote(service)}"))
    container_id = ""
    if rc == 0 and out.strip():
        container_id = out.splitlines()[0].strip()
    _SERVICE_CONTAINER_CACHE[service] = container_id
    return container_id


def get_service_runtime_status(service):
    container_id = get_service_container_id(service)
    if not container_id:
        return {
            "exists": False,
            "container_id": "",
            "state": "",
            "health": "",
        }

    rc, out, _ = run(
        f"docker inspect {shlex.quote(container_id)} --format "
        f"'{{{{.State.Status}}}}|{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{end}}}}'"
    )
    if rc != 0:
        return {
            "exists": True,
            "container_id": container_id,
            "state": "",
            "health": "",
        }

    raw_state, _, raw_health = out.strip().partition("|")
    return {
        "exists": True,
        "container_id": container_id,
        "state": raw_state.strip(),
        "health": raw_health.strip(),
    }


def compose_restart_cmd(service):
    return compose_cmd(f"restart {shlex.quote(service)}")


def get_service_recovery_cmd(service):
    runtime = get_service_runtime_status(service)
    if runtime["exists"]:
        return compose_restart_cmd(service)
    return compose_cmd(f"up -d {shlex.quote(service)}")


def get_service_log_since(service):
    if service in _SERVICE_LOG_SINCE_CACHE:
        return _SERVICE_LOG_SINCE_CACHE[service]

    container_id = get_service_container_id(service)
    if not container_id:
        _SERVICE_LOG_SINCE_CACHE[service] = "24h"
        return "24h"

    rc, out, _ = run(
        f"docker inspect {shlex.quote(container_id)} --format "
        f"'{{{{.State.StartedAt}}}}'"
    )
    since_value = out.strip() if rc == 0 and out.strip() else "24h"
    _SERVICE_LOG_SINCE_CACHE[service] = since_value
    return since_value


def describe_service_log_window(service):
    return (
        "desde o start atual do container"
        if get_service_log_since(service) != "24h"
        else "nas últimas 24h"
    )


def compose_logs_cmd(service, since_value=None):
    if since_value is None:
        since_value = get_service_log_since(service)
    return compose_cmd(
        f"logs --since {shlex.quote(since_value)} {shlex.quote(service)}"
    )


def is_benign_access_log(line):
    normalized = line.lower()
    return any(pattern in normalized for pattern in BENIGN_ACCESS_PATTERNS)


def _path_is_within(path, roots):
    try:
        resolved = Path(path).resolve(strict=False)
    except Exception:
        return False

    for root in roots:
        try:
            root_resolved = Path(root).resolve(strict=False)
        except Exception:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


SIZE_UNITS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
}

_DOCKER_BUILD_CACHE_BYTES_CACHE = None


def parse_human_size(raw_value):
    if not raw_value:
        return None

    normalized = raw_value.strip().upper().replace("IB", "B")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B|B)", normalized)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)
    return int(value * SIZE_UNITS[unit])


def format_bytes(num_bytes):
    if num_bytes is None:
        return "desconhecido"

    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)}{unit}"
            if value >= 10:
                return f"{value:.0f}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{int(num_bytes)}B"


def read_text_if_exists(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def get_docker_build_cache_bytes(force_refresh=False):
    global _DOCKER_BUILD_CACHE_BYTES_CACHE
    if _DOCKER_BUILD_CACHE_BYTES_CACHE is not None and not force_refresh:
        return _DOCKER_BUILD_CACHE_BYTES_CACHE

    rc, out, _ = run("docker system df -v 2>/dev/null", timeout=120)
    cache_bytes = 0
    if rc == 0 and out.strip():
        for line in out.splitlines():
            if line.startswith("Build cache usage:"):
                raw_value = line.split(":", 1)[1].strip()
                parsed = parse_human_size(raw_value)
                cache_bytes = parsed if parsed is not None else 0
                break

    _DOCKER_BUILD_CACHE_BYTES_CACHE = cache_bytes
    return cache_bytes


def list_zombie_processes():
    rc, out, _ = run("ps -eo pid=,ppid=,stat=,comm=,args= 2>/dev/null")
    if rc != 0 or not out.strip():
        return []

    zombies = []
    for line in out.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 4:
            continue
        pid_raw, ppid_raw, stat, comm = parts[:4]
        args = parts[4] if len(parts) > 4 else comm
        if not stat.startswith("Z"):
            continue
        try:
            pid = int(pid_raw)
            ppid = int(ppid_raw)
        except ValueError:
            continue
        zombies.append({
            "pid": pid,
            "ppid": ppid,
            "stat": stat,
            "comm": comm,
            "args": args,
        })
    return zombies


def get_process_command(pid):
    rc, out, _ = run(f"ps -p {int(pid)} -o args= 2>/dev/null")
    if rc == 0 and out.strip():
        return out.strip()
    return "desconhecido"


def http_get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return None, "", None, str(exc)

    try:
        parsed = json.loads(body) if body else None
    except Exception:
        parsed = None

    return status_code, body, parsed, ""


def credentials_git_rules_present():
    content = read_text_if_exists(REPO_GITIGNORE)
    if not content:
        return False
    lines = {line.strip() for line in content.splitlines()}
    return all(rule in lines for rule in CREDENTIALS_IGNORE_RULES)


def credentials_files_are_ignored():
    if not CREDENTIALS_DIR.exists():
        return False

    ignored_non_placeholder = False
    for path in CREDENTIALS_DIR.iterdir():
        if path.name == ".gitkeep":
            continue
        rel_path = path.relative_to(REPO_DIR)
        rc, _, _ = run(
            f"cd {shlex.quote(str(REPO_DIR))} && git check-ignore -q {shlex.quote(str(rel_path))}"
        )
        if rc != 0:
            return False
        ignored_non_placeholder = True

    return ignored_non_placeholder


def is_benign_credentials_git_status(line):
    return (
        line.strip() == "?? codigo/credentials/"
        and CREDENTIALS_GITKEEP.exists()
        and credentials_git_rules_present()
        and credentials_files_are_ignored()
    )


def classify_service_error_finding(service, error_count, sample_lines, window_desc):
    finding = {
        "component": service,
        "severity": ALTA if error_count > 10 else MEDIA,
        "type": APLICACAO,
        "description": f"{error_count} erros {window_desc}",
        "sample": sample_lines[:5],
        "auto_fix": False,
    }

    sample_text = "\n".join(sample_lines).lower()
    if (
        "duplicate key value violates unique constraint" in sample_text
        and "event_log" in sample_text
    ):
        finding["description"] = (
            f"{error_count} erros {window_desc} — duplicidade em secretario.event_log"
        )
        finding["root_cause"] = (
            "Fluxo tentou reinserir o mesmo event_id em secretario.event_log; "
            "o writer precisa fazer upsert/idempotência ao salvar EventLog."
        )
        finding["details"] = [
            "Padrão detectado localmente: duplicate key em secretario.event_log",
            *sample_lines[:3],
        ]

    return finding


def get_root_cause_text(finding):
    return finding.get("root_cause") or finding.get("ai_root_cause") or ""


def is_safe_fix_command(cmd):
    if not cmd:
        return False

    normalized = cmd.strip()
    if normalized == "docker image prune -f":
        return True
    if normalized == "docker builder prune -af":
        return True

    for service in MONITORED_SERVICES:
        if normalized == compose_cmd(f"up -d {shlex.quote(service)}"):
            return True
        if normalized == compose_restart_cmd(service):
            return True

    if normalized.startswith("kill -CHLD "):
        pid_raw = normalized[len("kill -CHLD "):].strip()
        return pid_raw.isdigit() and int(pid_raw) > 1

    if normalized.startswith("chmod "):
        parts = shlex.split(normalized)
        if len(parts) == 3 and parts[0] == "chmod" and parts[1] in {"600", "700"}:
            return _path_is_within(parts[2], [REPO_DIR, DOCTOR_DIR])

    return False


def verify_fix_effect(cmd):
    normalized = cmd.strip()

    if normalized == "docker builder prune -af":
        remaining = get_docker_build_cache_bytes(force_refresh=True)
        return remaining == 0, f"build cache restante: {format_bytes(remaining)}"

    if normalized.startswith("kill -CHLD "):
        pid_raw = normalized[len("kill -CHLD "):].strip()
        if not pid_raw.isdigit():
            return False, "pid invalido para verificacao de zumbi"
        parent_pid = int(pid_raw)
        remaining = [z for z in list_zombie_processes() if z["ppid"] == parent_pid]
        return not remaining, f"zumbis restantes com pai {parent_pid}: {len(remaining)}"

    for service in MONITORED_SERVICES:
        if normalized in {compose_cmd(f"up -d {shlex.quote(service)}"), compose_restart_cmd(service)}:
            runtime = get_service_runtime_status(service)
            state = runtime.get("state") or "desconhecido"
            health = runtime.get("health") or "n/a"
            running = state == "running"
            healthy = not runtime.get("health") or runtime.get("health") == "healthy"
            return running and healthy, f"estado={state}, health={health}"

    return None, ""


def log(phase, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}][Doctor][{phase}][{level}] {msg}")


def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def sha256_file(filepath):
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# MEMÓRIA
# ══════════════════════════════════════════════════════════════════

class DoctorMemory:
    """Memória própria do Doctor — persistente entre execuções."""

    def __init__(self):
        self.data = load_json(MEMORY_FILE, {
            "last_run": None,
            "file_hashes": {},
            "correction_history": [],
            "known_failures": [],
            "baseline_metrics": {},
            "run_count": 0,
            "repo_dir": str(REPO_DIR),
        })
        current_repo_dir = str(REPO_DIR.resolve(strict=False))
        previous_repo_dir = str(self.data.get("repo_dir") or "").strip()
        if previous_repo_dir and previous_repo_dir != current_repo_dir:
            log(
                "Memory",
                f"Repositório monitorado mudou de {previous_repo_dir} para {current_repo_dir}; resetando baseline de hashes",
                "WARN",
            )
            self.data["file_hashes"] = {}
            self.data["baseline_metrics"] = {}
        elif not previous_repo_dir and self.data.get("file_hashes"):
            log(
                "Memory",
                "Baseline legado sem repo_dir detectado; resetando hashes para o workspace atual",
                "WARN",
            )
            self.data["file_hashes"] = {}
            self.data["baseline_metrics"] = {}
        self.data["repo_dir"] = current_repo_dir

    def save(self):
        self.data["last_run"] = ISO_NOW
        self.data["run_count"] = self.data.get("run_count", 0) + 1
        self.data["repo_dir"] = str(REPO_DIR.resolve(strict=False))
        save_json(MEMORY_FILE, self.data)

    @property
    def hashes(self):
        return self.data.get("file_hashes", {})

    @hashes.setter
    def hashes(self, val):
        self.data["file_hashes"] = val

    def add_correction(self, entry):
        self.data.setdefault("correction_history", []).append(entry)

    def add_known_failure(self, failure):
        self.data.setdefault("known_failures", []).append(failure)

    def get_recurrence(self, component, description):
        """Conta quantas vezes essa falha já apareceu."""
        count = 0
        for f in self.data.get("known_failures", []):
            if f.get("component") == component and f.get("description") == description:
                count += 1
        return count


class SharedMemory:
    """Memória compartilhada — visível para outros agentes."""

    def __init__(self):
        self.data = load_json(SHARED_MEMORY_FILE, {
            "system_health": "UNKNOWN",
            "last_audit": None,
            "active_alerts": [],
            "human_intervention_needed": False,
            "intervention_details": None,
        })

    def save(self):
        self.data["last_audit"] = ISO_NOW
        save_json(SHARED_MEMORY_FILE, self.data)

    def set_health(self, status):
        self.data["system_health"] = status

    def add_alert(self, alert):
        self.data.setdefault("active_alerts", []).append(alert)

    def clear_alerts(self):
        self.data["active_alerts"] = []

    def request_human(self, details):
        self.data["human_intervention_needed"] = True
        self.data["intervention_details"] = details


# ══════════════════════════════════════════════════════════════════
# FASE 0 — CONSULTA PRÉVIA DE LOGS E HISTÓRICO
# ══════════════════════════════════════════════════════════════════

def fase0_consulta_previa(memory: DoctorMemory):
    """Coleta contexto de logs persistidos e histórico antes do diagnóstico."""
    log("Fase0", "Consultando logs persistidos e histórico...")
    context = {
        "previous_failures": memory.data.get("known_failures", [])[-20:],
        "last_run": memory.data.get("last_run"),
        "run_count": memory.data.get("run_count", 0),
        "log_errors_24h": [],
    }

    # Ler logs persistidos do sistema
    for log_dir in LOG_DIRS:
        if not log_dir.exists():
            continue
        for logfile in log_dir.glob("*.log"):
            try:
                lines = logfile.read_text(errors="replace").splitlines()
                # Últimas 500 linhas, filtrar erros
                recent = lines[-500:]
                errors = [
                    l for l in recent
                    if re.search(r"error|traceback|exception|500|403|timeout|critical|fatal", l, re.I)
                ]
                if errors:
                    context["log_errors_24h"].append({
                        "file": str(logfile),
                        "error_count": len(errors),
                        "sample": errors[-5:],  # últimos 5 erros
                    })
            except Exception:
                pass

    # Relatórios anteriores (últimos 7 dias)
    previous_reports = []
    if REPORT_DIR.exists():
        for rpt in sorted(REPORT_DIR.glob("report_*.json"))[-7:]:
            try:
                previous_reports.append(load_json(rpt))
            except Exception:
                pass
    context["previous_reports_count"] = len(previous_reports)

    log("Fase0", f"Contexto coletado: {len(context['log_errors_24h'])} arquivos com erros, "
                 f"{len(context['previous_failures'])} falhas conhecidas")
    return context


# ══════════════════════════════════════════════════════════════════
# FASE 1 — HEALTH CHECK
# ══════════════════════════════════════════════════════════════════

def fase1_health_check():
    """Verifica saúde dos containers, disco, memória, conectividade."""
    log("Fase1", "Health Check iniciando...")
    findings = []
    disk_pct = None

    # 1.1 — Docker disponível?
    rc, out, err = run("which docker")
    docker_available = rc == 0

    if not docker_available:
        # Tentar via socket direto
        docker_available = Path("/var/run/docker.sock").exists()
        if not docker_available:
            findings.append({
                "component": "docker",
                "severity": CRITICA,
                "type": INFRA,
                "description": "Docker CLI não encontrado e socket não disponível",
                "auto_fix": False,
            })
            log("Fase1", "Docker não disponível!", "ERROR")

    if docker_available:
        if not DOCKER_COMPOSE_FILE.exists():
            findings.append({
                "component": "docker-compose",
                "severity": CRITICA,
                "type": INFRA,
                "description": f"Compose file não encontrado em {DOCKER_COMPOSE_FILE}",
                "auto_fix": False,
            })
            log("Fase1", f"Compose file ausente: {DOCKER_COMPOSE_FILE}", "ERROR")
        else:
            # 1.2 — Status dos serviços
            rc, out, err = run(compose_cmd("ps --services --status running"))
            if rc == 0:
                running_services = {line.strip() for line in out.splitlines() if line.strip()}
                for service in MONITORED_SERVICES:
                    runtime = get_service_runtime_status(service)
                    optional = service in OPTIONAL_SERVICES
                    health = runtime.get("health", "")
                    if runtime["exists"] and runtime.get("state") == "running" and health == "unhealthy":
                        findings.append({
                            "component": service,
                            "severity": MEDIA if optional else ALTA,
                            "type": INFRA,
                            "description": (
                                f"Serviço Docker Compose '{service}' está unhealthy"
                                + (" (opcional)" if optional else "")
                            ),
                            "auto_fix": not optional,
                            "fix_cmd": None if optional else compose_restart_cmd(service),
                        })
                        log("Fase1", f"Serviço {service} unhealthy", "ERROR" if not optional else "WARN")
                    elif service not in running_services:
                        findings.append({
                            "component": service,
                            "severity": MEDIA if optional else CRITICA,
                            "type": INFRA,
                            "description": (
                                f"Serviço Docker Compose '{service}' não está rodando"
                                + (" (opcional)" if optional else "")
                            ),
                            "auto_fix": not optional,
                            "fix_cmd": None if optional else get_service_recovery_cmd(service),
                        })
                        state = runtime.get("state") or "ausente"
                        log("Fase1", f"Serviço {service} DOWN/estado={state}", "ERROR" if not optional else "WARN")
                    else:
                        log("Fase1", f"Serviço {service} OK")
            else:
                findings.append({
                    "component": "docker-compose",
                    "severity": ALTA,
                    "type": INFRA,
                    "description": "Falha ao consultar status dos serviços Docker Compose",
                    "details": [err[:200] or out[:200]],
                    "auto_fix": False,
                })
                log("Fase1", f"Falha ao consultar compose: {err[:120] or out[:120]}", "ERROR")

        # 1.3 — Logs de erro dos serviços (últimas 24h)
        for service in MONITORED_SERVICES:
            if not DOCKER_COMPOSE_FILE.exists():
                break
            since_value = get_service_log_since(service)
            window_desc = describe_service_log_window(service)
            rc, out, _ = run(
                f"{compose_logs_cmd(service, since_value)} 2>&1 | "
                f"grep -icE 'error|traceback|500|exception|timeout'"
            )
            if rc == 0 and out.strip():
                try:
                    error_count = int(out.strip())
                except ValueError:
                    error_count = 0
                if error_count > 0:
                    # Coletar amostra dos erros
                    _, sample, _ = run(
                        f"{compose_logs_cmd(service, since_value)} 2>&1 | "
                        f"grep -iE 'error|traceback|500|exception' | tail -5"
                    )
                    findings.append(
                        classify_service_error_finding(
                            service,
                            error_count,
                            sample.splitlines()[:5],
                            window_desc,
                        )
                    )

        # 1.3b — Smoke tests HTTP da aplicação
        smoke_checks = [
            {
                "component": "api-http-health",
                "url": f"{API_BASE_URL}/health",
                "expected_status": 200,
                "expected_json_status": {"healthy"},
                "severity": ALTA,
                "auto_fix": True,
            },
            {
                "component": "whatsapp-http-health",
                "url": f"{API_BASE_URL}/api/v1/webhooks/whatsapp/health",
                "expected_status": 200,
                "expected_json_status": {"operational", "healthy"},
                "severity": MEDIA,
                "auto_fix": True,
            },
        ]

        for smoke in smoke_checks:
            status_code, body, payload, error = http_get_json(smoke["url"], timeout=5)
            if error:
                findings.append({
                    "component": smoke["component"],
                    "severity": smoke["severity"],
                    "type": APLICACAO,
                    "description": f"Smoke test falhou em {smoke['url']}: {error}",
                    "auto_fix": smoke["auto_fix"],
                    "fix_cmd": get_service_recovery_cmd("api") if smoke["auto_fix"] else None,
                })
                continue

            if status_code != smoke["expected_status"]:
                findings.append({
                    "component": smoke["component"],
                    "severity": smoke["severity"],
                    "type": APLICACAO,
                    "description": f"Smoke test retornou HTTP {status_code} em {smoke['url']}",
                    "details": [body[:200]] if body else [],
                    "auto_fix": smoke["auto_fix"],
                    "fix_cmd": get_service_recovery_cmd("api") if smoke["auto_fix"] else None,
                })
                continue

            if isinstance(payload, dict):
                payload_status = str(payload.get("status", "")).lower()
                if payload_status and payload_status not in smoke["expected_json_status"]:
                    findings.append({
                        "component": smoke["component"],
                        "severity": smoke["severity"],
                        "type": APLICACAO,
                        "description": f"Smoke test retornou status '{payload_status}' em {smoke['url']}",
                        "details": [body[:200]] if body else [],
                        "auto_fix": False,
                    })

    # 1.4 — Disco
    rc, out, _ = run("df -h / | tail -1 | awk '{print $5}' | tr -d '%'")
    if rc == 0 and out.strip():
        try:
            disk_pct = int(out.strip())
            if disk_pct > 90:
                findings.append({
                    "component": "disco",
                    "severity": CRITICA,
                    "type": INFRA,
                    "description": f"Disco em {disk_pct}% — espaço crítico",
                    "auto_fix": False,
                })
            elif disk_pct > 80:
                findings.append({
                    "component": "disco",
                    "severity": ALTA,
                    "type": INFRA,
                    "description": f"Disco em {disk_pct}% — atenção",
                    "auto_fix": False,
                })
            log("Fase1", f"Disco: {disk_pct}%")
        except ValueError:
            pass

    # 1.5 — Memória
    rc, out, _ = run("free | awk '/Mem:/{printf \"%.0f\", $3/$2*100}'")
    if rc == 0 and out.strip():
        try:
            mem_pct = int(out.strip())
            if mem_pct > 90:
                findings.append({
                    "component": "memoria",
                    "severity": ALTA,
                    "type": INFRA,
                    "description": f"Memória RAM em {mem_pct}%",
                    "auto_fix": False,
                })
            log("Fase1", f"Memória: {mem_pct}%")
        except ValueError:
            pass

    # 1.6 — Build cache do Docker
    if docker_available:
        build_cache_bytes = get_docker_build_cache_bytes()
        if build_cache_bytes >= 1024 ** 3:
            findings.append({
                "component": "docker-build-cache",
                "severity": ALTA if build_cache_bytes >= 5 * (1024 ** 3) or (disk_pct or 0) >= 80 else MEDIA,
                "type": INFRA,
                "description": f"Build cache do Docker ocupando {format_bytes(build_cache_bytes)}",
                "auto_fix": True,
                "fix_cmd": "docker builder prune -af",
            })

    # 1.7 — Imagens Docker não utilizadas (problema do Hely)
    rc, out, _ = run("docker images --filter 'dangling=true' -q | wc -l")
    if rc == 0 and out.strip():
        try:
            dangling = int(out.strip())
            if dangling > 5:
                findings.append({
                    "component": "docker-images",
                    "severity": MEDIA,
                    "type": INFRA,
                    "description": f"{dangling} imagens Docker órfãs ocupando espaço",
                    "auto_fix": True,
                    "fix_cmd": "docker image prune -f",
                })
        except ValueError:
            pass

    # 1.8 — Total de imagens Docker
    rc, out, _ = run("docker images -q | wc -l")
    if rc == 0 and out.strip():
        try:
            total_images = int(out.strip())
            if total_images > 20:
                _, size_out, _ = run("docker system df --format '{{.Size}}' 2>/dev/null | head -1")
                findings.append({
                    "component": "docker-images",
                    "severity": BAIXA,
                    "type": INFRA,
                    "description": f"{total_images} imagens Docker no total. Espaço usado: {size_out}",
                    "auto_fix": False,
                })
        except ValueError:
            pass

    # 1.9 — Processos zumbi
    zombies = list_zombie_processes()
    zombies_by_parent = {}
    for zombie in zombies:
        zombies_by_parent.setdefault(zombie["ppid"], []).append(zombie)

    for parent_pid, zombie_group in sorted(zombies_by_parent.items()):
        parent_cmd = get_process_command(parent_pid)
        findings.append({
            "component": "zombie-process",
            "severity": ALTA if len(zombie_group) >= 3 or parent_pid == 1 else MEDIA,
            "type": INFRA,
            "description": f"{len(zombie_group)} processo(s) zumbi com pai PID {parent_pid}",
            "details": [
                f"parent_cmd={parent_cmd[:140]}",
                *[
                    f"pid={z['pid']} stat={z['stat']} comm={z['comm']}"
                    for z in zombie_group[:5]
                ],
            ],
            "auto_fix": parent_pid > 1,
            "fix_cmd": f"kill -CHLD {parent_pid}" if parent_pid > 1 else None,
        })

    log("Fase1", f"Health Check concluído: {len(findings)} achados")
    return findings


# ══════════════════════════════════════════════════════════════════
# FASE 2 — SECURITY AUDIT (integrado + expandido)
# ══════════════════════════════════════════════════════════════════

# Padrões de tokens (expandido do security_audit.py original)
TOKEN_PATTERNS = {
    "openai": r"sk-[a-zA-Z0-9]{20,}",
    "github_pat": r"ghp_[a-zA-Z0-9]{36}",
    "github_fine": r"github_pat_[a-zA-Z0-9_]{60,}",
    "slack_bot": r"xoxb-[0-9]{11}-[0-9]{11}-[a-zA-Z0-9]{24}",
    "slack_user": r"xoxp-[0-9]+-[0-9]+-[0-9]+-[a-f0-9]+",
    "aws_access": r"AKIA[0-9A-Z]{16}",
    "telegram_bot": r"[0-9]{8,10}:[a-zA-Z0-9_-]{35}",
    "google_api": r"AIza[0-9A-Za-z_-]{35}",
    "deepseek": r"sk-[a-f0-9]{32,}",
    "anthropic": r"sk-ant-[a-zA-Z0-9-]{20,}",
    "nvidia": r"nvapi-[a-zA-Z0-9_-]{20,}",
    "generic_long": r"[a-zA-Z0-9]{40,}",
}

CONFIG_CREDENTIAL_PATTERNS = [
    r'"api[_-]?[Kk]ey"\s*:\s*"[^"]{8,}"',
    r'"token"\s*:\s*"[^"]{8,}"',
    r'"password"\s*:\s*"[^"]{4,}"',
    r'"secret"\s*:\s*"[^"]{8,}"',
    r'"PRIVATE[_-]KEY"\s*:\s*"[^"]+"',
    r'(?:PASSWORD|SECRET|TOKEN|API_KEY)\s*=\s*\S{8,}',
]


def fase2_security_audit(memory: DoctorMemory):
    """Auditoria de segurança: hashes, tokens, git, acessos."""
    log("Fase2", "Security Audit iniciando...")
    findings = []

    # 2.1 — Hash de arquivos críticos
    current_hashes = {}
    if REPO_DIR.exists():
        for pattern in CRITICAL_FILE_PATTERNS:
            for filepath in REPO_DIR.rglob(pattern):
                # Ignorar venv, node_modules, __pycache__, .git
                skip = any(p in filepath.parts for p in
                           ["venv", "node_modules", "__pycache__", ".git", "migrations"])
                if skip:
                    continue
                rel = str(filepath.relative_to(REPO_DIR))
                h = sha256_file(filepath)
                if h:
                    current_hashes[rel] = h

    old_hashes = memory.hashes
    if old_hashes:
        # Arquivos alterados
        changed = []
        for f, h in current_hashes.items():
            if f in old_hashes and old_hashes[f] != h:
                changed.append(f)

        # Arquivos novos
        added = [f for f in current_hashes if f not in old_hashes]

        # Arquivos removidos
        removed = [f for f in old_hashes if f not in current_hashes]

        if changed:
            findings.append({
                "component": "code-integrity",
                "severity": ALTA,
                "type": SEGURANCA,
                "description": f"{len(changed)} arquivo(s) alterado(s) desde último ciclo",
                "details": changed[:20],
                "auto_fix": False,
            })
            log("Fase2", f"Arquivos alterados: {changed[:5]}", "WARN")

        if removed:
            findings.append({
                "component": "code-integrity",
                "severity": MEDIA,
                "type": SEGURANCA,
                "description": f"{len(removed)} arquivo(s) removido(s)",
                "details": removed[:10],
                "auto_fix": False,
            })
    else:
        log("Fase2", "Primeira execução — criando baseline de hashes")

    # Atualizar baseline
    memory.hashes = current_hashes

    # 2.2 — Git diff com remoto
    if REPO_DIR.exists() and (REPO_DIR / ".git").exists():
        run(f"cd {REPO_DIR} && git fetch origin 2>/dev/null", timeout=30)
        rc, diff_out, _ = run(f"cd {REPO_DIR} && git diff HEAD origin/main --stat 2>/dev/null")
        if rc == 0 and diff_out.strip():
            findings.append({
                "component": "git-sync",
                "severity": MEDIA,
                "type": SEGURANCA,
                "description": "Código local diverge do repositório remoto",
                "details": diff_out.splitlines()[-3:],
                "auto_fix": False,
            })

        # Alterações não commitadas
        rc, status_out, _ = run(f"cd {REPO_DIR} && git status --porcelain 2>/dev/null")
        if rc == 0 and status_out.strip():
            uncommitted = status_out.splitlines()
            filtered_uncommitted = [
                line for line in uncommitted if not is_benign_credentials_git_status(line)
            ]
            if filtered_uncommitted:
                findings.append({
                    "component": "git-uncommitted",
                    "severity": BAIXA,
                    "type": SEGURANCA,
                    "description": f"{len(filtered_uncommitted)} arquivo(s) com alterações não commitadas",
                    "details": filtered_uncommitted[:10],
                    "auto_fix": False,
                })

    # 2.3 — Tokens expostos em variáveis de ambiente
    env_exposed = []
    safe_keys = {"HOME", "PATH", "SHELL", "TERM", "USER", "LANG", "PWD", "HOSTNAME"}
    for key, value in os.environ.items():
        if key in safe_keys or len(value) < 8:
            continue
        for token_type, pattern in TOKEN_PATTERNS.items():
            if token_type == "generic_long":
                continue  # Muito barulhento, usar só para config files
            if re.search(pattern, value):
                env_exposed.append({
                    "key": key,
                    "token_type": token_type,
                    "preview": value[:8] + "..."
                })
                break

    if env_exposed:
        findings.append({
            "component": "env-tokens",
            "severity": MEDIA,
            "type": SEGURANCA,
            "description": f"{len(env_exposed)} token(s) em variáveis de ambiente",
            "details": [f"{e['key']} ({e['token_type']})" for e in env_exposed],
            "auto_fix": False,
        })

    # 2.4 — Credenciais em arquivos de config
    config_exposed = []
    config_files_to_check = list(REPO_DIR.glob("*.json"))
    config_files_to_check += list(REPO_CODE_DIR.rglob("*.json"))
    for cf in config_files_to_check:
        try:
            content = cf.read_text(errors="replace")
            for pattern in CONFIG_CREDENTIAL_PATTERNS:
                matches = re.findall(pattern, content, re.I)
                for m in matches:
                    config_exposed.append({
                        "file": str(cf.relative_to(REPO_DIR)),
                        "match_preview": m[:50] + "..." if len(m) > 50 else m,
                    })
        except Exception:
            pass

    if config_exposed:
        findings.append({
            "component": "config-credentials",
            "severity": ALTA,
            "type": SEGURANCA,
            "description": f"{len(config_exposed)} credencial(is) em arquivos de configuração",
            "details": [f"{e['file']}: {e['match_preview']}" for e in config_exposed[:10]],
            "auto_fix": False,
        })

    # 2.5 — Tokens inválidos / tentativas de acesso nos logs
    invalid_tokens = 0
    if any(Path(d).exists() for d in LOG_DIRS):
        for service in MONITORED_SERVICES:
            if not DOCKER_COMPOSE_FILE.exists():
                break
            since_value = get_service_log_since(service)
            _, out, _ = run(
                f"{compose_logs_cmd(service, since_value)} 2>&1 | "
                f"grep -iE 'invalid.token|forbidden|unauthorized|verify_token' 2>/dev/null || true"
            )
            matched_lines = [line for line in out.splitlines() if line.strip()]
            suspicious_lines = [line for line in matched_lines if not is_benign_access_log(line)]
            invalid_tokens += len(suspicious_lines)

    if invalid_tokens > 0:
        severity = CRITICA if invalid_tokens > 20 else (ALTA if invalid_tokens > 5 else MEDIA)
        findings.append({
            "component": "access-attempts",
            "severity": severity,
            "type": SEGURANCA,
            "description": f"{invalid_tokens} tentativa(s) com token inválido nas últimas 24h",
            "auto_fix": False,
        })

    # 2.6 — Permissões de arquivos sensíveis
    sensitive_files = list(REPO_DIR.glob("**/.env")) + list(REPO_DIR.glob("**/credentials*"))
    if CREDENTIALS_DIR.exists():
        sensitive_files.extend(path for path in CREDENTIALS_DIR.rglob("*") if path.is_file())
    for sf in sensitive_files:
        if sf.exists():
            expected_modes = ("700", "750") if sf.is_dir() else ("600", "400", "640")
            mode = oct(sf.stat().st_mode)[-3:]
            if mode not in expected_modes:
                expected_mode = "700" if sf.is_dir() else "600"
                findings.append({
                    "component": "file-permissions",
                    "severity": ALTA,
                    "type": SEGURANCA,
                    "description": f"Permissão {mode} em {sf.name} — deveria ser {expected_mode}",
                    "auto_fix": True,
                    "fix_cmd": f"chmod {expected_mode} {sf}",
                })

    # 2.7 — Processos suspeitos (simples)
    rc, out, _ = run("ps aux | grep -vE 'grep|docker|python|node|celery|redis|postgres|cloudflared|cron|sshd|bash|systemd' | tail -20")
    # (apenas informativo, registra no relatório)

    log("Fase2", f"Security Audit concluído: {len(findings)} achados")
    return findings


# ══════════════════════════════════════════════════════════════════
# FASE 3 — DIAGNÓSTICO E CLASSIFICAÇÃO
# ══════════════════════════════════════════════════════════════════

def fase3_diagnostico(all_findings, memory: DoctorMemory, brain: DoctorBrain, context):
    """Classifica com IA, marca recorrência, prioriza."""
    log("Fase3", "Diagnosticando e classificando...")

    # Marcar recorrência (lógica local, não depende de IA)
    for f in all_findings:
        recurrence = memory.get_recurrence(f["component"], f["description"])
        f["recurrence"] = recurrence
        if recurrence >= 3:
            f["chronic"] = True
            if f["severity"] in (BAIXA, MEDIA):
                f["severity"] = ALTA
        else:
            f["chronic"] = False

        memory.add_known_failure({
            "component": f["component"],
            "description": f["description"],
            "severity": f["severity"],
            "timestamp": ISO_NOW,
        })

    # ── Análise com IA ──
    ai_diagnostics = None
    ai_provider = None
    if all_findings:
        log("Fase3", "Consultando IA para diagnóstico de causa raiz...")
        ai_diagnostics, ai_provider = brain.analyze_findings(all_findings, context)

        if ai_diagnostics and "diagnostics" in ai_diagnostics:
            log("Fase3", f"IA ({ai_provider}) retornou diagnóstico para {len(ai_diagnostics['diagnostics'])} findings")
            for diag in ai_diagnostics["diagnostics"]:
                idx = diag.get("finding_index", -1)
                if 0 <= idx < len(all_findings):
                    all_findings[idx]["ai_root_cause"] = diag.get("root_cause", "")
                    all_findings[idx]["ai_risk"] = diag.get("risk_if_ignored", "")
                    # IA pode sugerir correções, mas elas ficam apenas para revisão humana.
                    if diag.get("is_safe_to_autofix") and diag.get("fix_command"):
                        all_findings[idx]["ai_suggested_fix_cmd"] = diag["fix_command"]
                        all_findings[idx]["ai_fix_explanation"] = diag.get("fix_explanation", "")
                        log("Fase3", f"  IA sugeriu fix para revisão: {all_findings[idx]['component']}")
                    # IA pode ajustar severidade
                    adjusted = diag.get("severity_adjusted")
                    if adjusted and adjusted in (CRITICA, ALTA, MEDIA, BAIXA):
                        all_findings[idx]["severity"] = adjusted

            # Avaliação de ameaça de segurança
            security_findings = [f for f in all_findings if f["type"] == SEGURANCA]
            if security_findings:
                log("Fase3", "Consultando IA para avaliação de ameaça...")
                threat, _ = brain.evaluate_security_threat(security_findings)
                if threat and threat.get("is_invasion"):
                    log("Fase3", "🚨 IA detectou possível INVASÃO!", "CRITICAL")
                    for sf in security_findings:
                        sf["severity"] = CRITICA
                        sf["auto_fix"] = False  # Nunca autocorrigir invasão
                    all_findings.insert(0, {
                        "component": "INVASÃO-DETECTADA",
                        "severity": CRITICA,
                        "type": SEGURANCA,
                        "description": f"IA detectou invasão (confiança: {threat.get('confidence', '?')})",
                        "details": threat.get("evidence", []),
                        "auto_fix": False,
                        "recurrence": 0,
                        "chronic": False,
                    })
                ai_diagnostics["threat_assessment"] = threat
        elif ai_provider:
            schema_status = getattr(brain, "last_schema_status", "unknown")
            preview = (getattr(brain, "last_raw_response", "") or "").replace("\n", " ")[:180]
            log(
                "Fase3",
                f"IA ({ai_provider}) respondeu, mas sem schema utilizável ({schema_status}); mantendo classificação por regras. Preview: {preview}",
                "WARN",
            )
        else:
            log("Fase3", "IA indisponível — usando classificação por regras", "WARN")

    duplicate_event_log_detected = any(
        "secretario.event_log" in f.get("description", "")
        or "secretario.event_log" in get_root_cause_text(f)
        for f in all_findings
    )
    if duplicate_event_log_detected:
        for f in all_findings:
            if f["component"] == "api" and not get_root_cause_text(f):
                f["root_cause"] = (
                    "Erro reflexo da mesma duplicidade em secretario.event_log "
                    "durante o fluxo de processamento."
                )

    # Ordenar por severidade
    severity_order = {CRITICA: 0, ALTA: 1, MEDIA: 2, BAIXA: 3}
    all_findings.sort(key=lambda x: severity_order.get(x["severity"], 99))

    log("Fase3", f"Diagnóstico concluído: {len(all_findings)} falhas classificadas")
    return all_findings, ai_diagnostics, ai_provider


# ══════════════════════════════════════════════════════════════════
# FASE 4 — AUTOCORREÇÃO
# ══════════════════════════════════════════════════════════════════

def fase4_autocorrecao(findings, memory: DoctorMemory):
    """Aplica correções automáticas quando possível."""
    log("Fase4", "Autocorreção iniciando...")
    actions = []

    for f in findings:
        if not f.get("auto_fix"):
            continue

        fix_cmd = f.get("fix_cmd")
        if not fix_cmd:
            continue

        if f.get("fix_source") == "IA":
            actions.append({
                "failure_ref": f["component"],
                "description": f["description"],
                "command": fix_cmd,
                "fix_source": "IA",
                "result": "BLOQUEADO",
                "output": "Sugestões da IA exigem revisão humana",
                "timestamp": ISO_NOW,
            })
            f["auto_fix"] = False
            log("Fase4", f"Fix de IA bloqueado para revisão humana: {f['component']}", "WARN")
            continue

        if not is_safe_fix_command(fix_cmd):
            actions.append({
                "failure_ref": f["component"],
                "description": f["description"],
                "command": fix_cmd,
                "fix_source": f.get("fix_source", "regra"),
                "result": "BLOQUEADO",
                "output": "Comando fora da allowlist de autocorreção",
                "timestamp": ISO_NOW,
            })
            f["auto_fix"] = False
            log("Fase4", f"Comando bloqueado fora da allowlist: {fix_cmd[:120]}", "WARN")
            continue

        log("Fase4", f"Corrigindo: {f['component']} — {f['description']}")

        rc, out, err = run(fix_cmd, timeout=120)
        success = rc == 0
        verification_note = ""
        if success:
            verified_success, verification_note = verify_fix_effect(fix_cmd)
            if verified_success is not None:
                success = verified_success

        output_excerpt = (out or err)[:200]
        if verification_note:
            output_excerpt = (
                f"{verification_note} | {output_excerpt}"
                if output_excerpt
                else verification_note
            )

        action = {
            "failure_ref": f["component"],
            "description": f["description"],
            "command": fix_cmd,
            "fix_source": f.get("fix_source", "regra"),
            "result": "OK" if success else "FALHA",
            "output": output_excerpt,
            "timestamp": ISO_NOW,
        }
        actions.append(action)
        memory.add_correction(action)

        if success:
            log("Fase4", f"  ✅ Correção aplicada com sucesso")
        else:
            log("Fase4", f"  ❌ Correção falhou: {err[:100]}", "ERROR")
            f["auto_fix"] = False  # Marcar para intervenção humana

    log("Fase4", f"Autocorreção concluída: {len(actions)} ações")
    return actions


# ══════════════════════════════════════════════════════════════════
# FASE 5 — RELATÓRIO
# ══════════════════════════════════════════════════════════════════

def fase5_relatorio(findings, actions, context, brain: DoctorBrain,
                    ai_diagnostics=None, ai_provider=None):
    """Gera relatório completo em Markdown e JSON, com narrativa da IA."""
    log("Fase5", "Gerando relatório...")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    needs_human = [f for f in findings if not f.get("auto_fix") and f["severity"] in (CRITICA, ALTA)]
    max_severity = findings[0]["severity"] if findings else "NENHUMA"

    # ── Narrativa gerada por IA ──
    narrative_data, narr_provider = brain.generate_report_narrative(
        findings, actions, ai_diagnostics
    )
    narrative = narrative_data.get("narrative", "") if narrative_data else ""
    oneliner = narrative_data.get("summary_oneliner", "") if narrative_data else ""

    # --- Markdown ---
    md_lines = [
        f"# Relatório Doctor — {NOW.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"> {oneliner}" if oneliner else "",
        "",
        "## Resumo",
        f"- **Execução:** {ISO_NOW}",
        f"- **IA utilizada:** {ai_provider or 'nenhuma (fallback regras)'}",
        f"- **Falhas encontradas:** {len(findings)}",
        f"- **Correções aplicadas:** {len([a for a in actions if a['result'] == 'OK'])}",
        f"- **Correções falharam:** {len([a for a in actions if a['result'] != 'OK'])}",
        f"- **Correções sugeridas por IA:** {len([f for f in findings if f.get('ai_suggested_fix_cmd')])}",
        f"- **Pendentes intervenção humana:** {len(needs_human)}",
        f"- **Severidade máxima:** {max_severity}",
        "",
    ]

    if narrative:
        md_lines += ["## Análise do Doctor (IA)", "", narrative, ""]

    md_lines += [
        "## Falhas Encontradas",
        "",
        "| # | Componente | Severidade | Tipo | Descrição | Causa Raiz | Recorrente? | Auto? |",
        "|---|-----------|-----------|------|-----------|-------------|-------------|-------|",
    ]

    for i, f in enumerate(findings, 1):
        rec = f"SIM ({f['recurrence']}x)" if f.get("recurrence", 0) > 0 else "NÃO"
        auto = "SIM" if f.get("auto_fix") else "NÃO"
        root = (get_root_cause_text(f) or "-")[:40]
        md_lines.append(
            f"| {i} | {f['component']} | {f['severity']} | {f['type']} | "
            f"{f['description'][:50]} | {root} | {rec} | {auto} |"
        )

    if actions:
        md_lines += [
            "", "## Ações Tomadas", "",
            "| # | Componente | Fonte | Ação | Resultado |",
            "|---|-----------|-------|------|-----------|",
        ]
        for i, a in enumerate(actions, 1):
            fonte = a.get("fix_source", "regra")
            md_lines.append(
                f"| {i} | {a['failure_ref']} | {fonte} | `{a['command'][:40]}` | {a['result']} |"
            )

    if needs_human:
        md_lines += [
            "", "## ⚠️ Intervenção Humana Necessária", "",
        ]
        for f in needs_human:
            md_lines.append(f"- **[{f['severity']}] {f['component']}:** {f['description']}")
            if get_root_cause_text(f):
                md_lines.append(f"  - *Causa raiz:* {get_root_cause_text(f)}")
            if f.get("ai_risk"):
                md_lines.append(f"  - *Risco se ignorado:* {f['ai_risk']}")
            if f.get("details"):
                for d in f["details"][:3]:
                    md_lines.append(f"  - `{d}`")

    # Recomendações da IA
    if ai_diagnostics and ai_diagnostics.get("recommendations"):
        md_lines += ["", "## Recomendações (IA)", ""]
        for r in ai_diagnostics["recommendations"]:
            md_lines.append(f"- {r}")

    if ai_diagnostics and ai_diagnostics.get("patterns_detected"):
        md_lines += ["", "## Padrões Detectados (IA)", ""]
        for p in ai_diagnostics["patterns_detected"]:
            md_lines.append(f"- {p}")

    md_lines += ["", "---", f"*Gerado pelo Doctor em {ISO_NOW} | IA: {ai_provider or 'N/A'}*"]

    md_content = "\n".join(md_lines)
    md_path = REPORT_DIR / f"report_{TIMESTAMP}.md"
    md_path.write_text(md_content)

    # --- JSON ---
    json_report = {
        "timestamp": ISO_NOW,
        "ai_provider": ai_provider,
        "summary": {
            "total_findings": len(findings),
            "corrections_ok": len([a for a in actions if a["result"] == "OK"]),
            "corrections_failed": len([a for a in actions if a["result"] != "OK"]),
            "ai_suggested_fixes": len([f for f in findings if f.get("ai_suggested_fix_cmd")]),
            "needs_human": len(needs_human),
            "max_severity": max_severity,
            "oneliner": oneliner,
        },
        "narrative": narrative,
        "findings": findings,
        "actions": actions,
        "ai_diagnostics": ai_diagnostics,
        "human_intervention": [
            {"component": f["component"], "severity": f["severity"],
             "description": f["description"], "root_cause": get_root_cause_text(f)}
            for f in needs_human
        ],
    }
    json_path = REPORT_DIR / f"report_{TIMESTAMP}.json"
    save_json(json_path, json_report)

    log("Fase5", f"Relatório salvo: {md_path}")
    return md_path, json_path, needs_human, max_severity


# ══════════════════════════════════════════════════════════════════
# FASE 6 — ESCALAÇÃO (via Aisha)
# ══════════════════════════════════════════════════════════════════

def fase6_escalacao(needs_human, max_severity, report_path, shared: SharedMemory):
    """Solicita que Aisha entre em contato com Hely se necessário."""
    if not needs_human:
        shared.set_health("OK")
        log("Fase6", "Nenhuma escalação necessária — sistema OK")
        return

    shared.set_health("DEGRADED" if max_severity != CRITICA else "CRITICAL")
    details = "\n".join(
        f"[{f['severity']}] {f['component']}: {f['description']}"
        for f in needs_human
    )
    shared.request_human(details)
    for f in needs_human:
        shared.add_alert({
            "component": f["component"],
            "severity": f["severity"],
            "description": f["description"],
            "timestamp": ISO_NOW,
        })

    payload = json.dumps({
        "severity": max_severity,
        "message": f"🏥 Doctor encontrou {len(needs_human)} item(ns) que precisam de atenção:\n\n{details}",
        "report_path": str(report_path),
    })

    if not AISHA_NOTIFY_ENDPOINT:
        save_json(PENDING_NOTIFICATION_FILE, {
            "timestamp": ISO_NOW,
            "severity": max_severity,
            "details": details,
            "report_path": str(report_path),
            "delivery": "pending_local",
            "reason": "AISHA_NOTIFY_ENDPOINT não configurado",
        })
        log("Fase6", f"Sem endpoint configurado; notificação salva em {PENDING_NOTIFICATION_FILE}", "WARN")
        return

    # Tentar notificar Aisha via HTTP quando explicitamente configurado.

    rc, out, err = run(
        f"curl -fsS -X POST {AISHA_NOTIFY_ENDPOINT} "
        f"-H 'Content-Type: application/json' "
        f"-d '{payload}' --connect-timeout 10",
        timeout=15,
    )

    if rc == 0:
        log("Fase6", "Aisha notificada com sucesso")
    else:
        log("Fase6", f"Falha ao notificar Aisha: {err}", "WARN")
        save_json(PENDING_NOTIFICATION_FILE, {
            "timestamp": ISO_NOW,
            "severity": max_severity,
            "details": details,
            "report_path": str(report_path),
            "delivery": "pending_local",
            "reason": err[:200] or out[:200],
        })
        log("Fase6", f"Notificação pendente salva em {PENDING_NOTIFICATION_FILE}")


# ══════════════════════════════════════════════════════════════════
# MAIN — ORQUESTRADOR
# ══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"  🏥 DOCTOR — Ciclo iniciado: {ISO_NOW}")
    print(f"  Repo: {REPO_DIR}")
    print(f"  Dados: {DOCTOR_DIR}")
    print("=" * 60)

    # Inicializar memórias e cérebro IA
    DOCTOR_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DOCTOR_LOG_DIR.mkdir(parents=True, exist_ok=True)

    memory = DoctorMemory()
    shared = SharedMemory()
    shared.clear_alerts()
    brain = DoctorBrain()

    # Executar fases
    context = fase0_consulta_previa(memory)
    health_findings = fase1_health_check()
    security_findings = fase2_security_audit(memory)

    all_findings = health_findings + security_findings
    all_findings, ai_diagnostics, ai_provider = fase3_diagnostico(
        all_findings, memory, brain, context
    )

    actions = fase4_autocorrecao(all_findings, memory)

    md_path, json_path, needs_human, max_severity = fase5_relatorio(
        all_findings, actions, context, brain, ai_diagnostics, ai_provider
    )

    fase6_escalacao(needs_human, max_severity, md_path, shared)

    # Salvar memórias
    memory.save()
    shared.save()

    print("=" * 60)
    print(f"  🏥 DOCTOR — Ciclo finalizado: {datetime.now().isoformat()}")
    print(f"  IA utilizada: {ai_provider or 'nenhuma'}")
    print(f"  Relatório: {md_path}")
    print(f"  Falhas: {len(all_findings)} | Corrigidas: {len(actions)}")
    print(f"  Intervenção humana: {'SIM' if needs_human else 'NÃO'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
