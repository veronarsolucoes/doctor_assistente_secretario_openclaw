#!/usr/bin/env python3
"""
doctor_brain.py — Cérebro de IA do Doctor
Cadeia de fallback: Claude API → Gemini API → Ollama local ou nuvem
Cada provider é tentado em ordem; se falhar, passa ao próximo.
"""

import os
import json
import urllib.request
import urllib.error
import ssl

# ══════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO DOS PROVIDERS
# ══════════════════════════════════════════════════════════════════

AI_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_AI_API_KEY",
    "NVIDIA_API_KEY",
    "OLLAMA_URL",
    "OLLAMA_BASE_URL",
    "OLLAMA_API_KEY",
    "OLLAMA_MODEL",
    "OLLAMA_MODE",
}


def _parse_env_value(raw_value):
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_env_file_if_present(path):
    if not path or not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key not in AI_ENV_KEYS or key in os.environ:
                continue
            os.environ[key] = _parse_env_value(raw_value)


def _strip_provider_prefix(model_ref, provider_name):
    if not isinstance(model_ref, str):
        return None
    prefix = provider_name + "/"
    if model_ref.startswith(prefix):
        return model_ref[len(prefix):]
    return model_ref


def _collect_default_models(data, provider_name):
    defaults = (((data.get("agents") or {}).get("defaults") or {}).get("model") or {})
    candidates = []

    primary = _strip_provider_prefix(defaults.get("primary"), provider_name)
    if primary and primary != defaults.get("primary"):
        candidates.append(primary)

    for fallback in defaults.get("fallbacks") or []:
        normalized = _strip_provider_prefix(fallback, provider_name)
        if normalized and normalized != fallback:
            candidates.append(normalized)

    return candidates


def _pick_provider_model(data, provider_name, provider, preferred_models=None, default_model=None):
    preferred_models = preferred_models or []
    available_models = []
    for model in provider.get("models") or []:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id.strip():
            available_models.append(model_id.strip())

    for candidate in preferred_models + _collect_default_models(data, provider_name):
        if available_models:
            if candidate in available_models:
                return candidate
            continue
        if candidate:
            return candidate

    if available_models:
        return available_models[0]
    return default_model


def _load_openclaw_config_if_present(path):
    if not path or not os.path.isfile(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return

    providers = (data.get("models") or {}).get("providers") or {}
    ollama_provider = providers.get("ollama") or {}
    if not isinstance(ollama_provider, dict):
        ollama_provider = {}

    base_url = ollama_provider.get("baseUrl")
    if isinstance(base_url, str) and base_url.strip() and "OLLAMA_BASE_URL" not in os.environ:
        os.environ["OLLAMA_BASE_URL"] = base_url.strip()

    api_key = ollama_provider.get("apiKey")
    if (
        isinstance(api_key, str)
        and api_key.strip()
        and not api_key.strip().startswith("$")
        and "OLLAMA_API_KEY" not in os.environ
    ):
        os.environ["OLLAMA_API_KEY"] = api_key.strip()

    if "OLLAMA_MODEL" not in os.environ:
        model = _pick_provider_model(
            data,
            "ollama",
            ollama_provider,
            preferred_models=["minimax-m2.7", "minimax-m2.5", "gemma4:31b", "gemini-3-flash-preview:cloud", "kimi-k2.5"],
            default_model="qwen2.5:7b",
        )
        if model:
            os.environ["OLLAMA_MODEL"] = model

    googleai_provider = providers.get("googleai") or {}
    if isinstance(googleai_provider, dict):
        base_url = googleai_provider.get("baseUrl")
        if (
            isinstance(base_url, str)
            and base_url.strip()
            and "OPENCLAW_GOOGLEAI_BASE_URL" not in os.environ
        ):
            os.environ["OPENCLAW_GOOGLEAI_BASE_URL"] = base_url.strip()

        if "OPENCLAW_GOOGLEAI_MODEL" not in os.environ:
            model = _pick_provider_model(
                data,
                "googleai",
                googleai_provider,
                preferred_models=["gemini-2.5-flash", "gemini-2.5-pro", "gemma-4-31b-it"],
                default_model="gemini-2.5-flash",
            )
            if model:
                os.environ["OPENCLAW_GOOGLEAI_MODEL"] = model

    nvidia_provider = providers.get("nvidia") or {}
    if isinstance(nvidia_provider, dict):
        base_url = nvidia_provider.get("baseUrl")
        if (
            isinstance(base_url, str)
            and base_url.strip()
            and "OPENCLAW_NVIDIA_BASE_URL" not in os.environ
        ):
            os.environ["OPENCLAW_NVIDIA_BASE_URL"] = base_url.strip()

        if "OPENCLAW_NVIDIA_MODEL" not in os.environ:
            model = _pick_provider_model(
                data,
                "nvidia",
                nvidia_provider,
                preferred_models=["moonshotai/kimi-k2.5", "qwen/qwen3.5-397b-a17b"],
                default_model="moonshotai/kimi-k2.5",
            )
            if model:
                os.environ["OPENCLAW_NVIDIA_MODEL"] = model


def _bootstrap_openclaw_ai_env():
    env_candidates = [
        os.getenv("OPENCLAW_ENV_FILE", "").strip(),
        "/opt/openclaw/.env",
        os.path.expanduser("~/.openclaw/.env"),
    ]
    seen = set()
    for candidate in env_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        _load_env_file_if_present(candidate)

    config_candidates = [
        os.getenv("OPENCLAW_CONFIG_PATH", "").strip(),
        os.path.expanduser("~/.openclaw/openclaw.json"),
    ]
    seen.clear()
    for candidate in config_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        _load_openclaw_config_if_present(candidate)


def _normalize_base_url(base_url):
    return base_url.rstrip("/")


def _build_openai_compat_provider(name, base_url_env, model_env, api_key_env, default_model):
    base_url = os.getenv(base_url_env, "").strip()
    model = os.getenv(model_env, default_model).strip() or default_model
    return {
        "name": name,
        "enabled": bool(base_url),
        "api_mode": "openai",
        "api_key_env": api_key_env,
        "url": (_normalize_base_url(base_url) + "/chat/completions") if base_url else "",
        "model": model,
        "max_tokens": 4096,
    }


def _apply_provider_priority(providers):
    """Reordena providers conforme preferência explícita no ambiente."""
    primary = os.getenv("DOCTOR_AI_PRIMARY_PROVIDER", "").strip().lower()
    order_raw = os.getenv("DOCTOR_AI_PROVIDER_ORDER", "").strip().lower()

    ordered_names = []
    if primary:
        ordered_names.append(primary)
    if order_raw:
        ordered_names.extend(name.strip() for name in order_raw.split(",") if name.strip())

    if not ordered_names:
        return providers

    priority = {}
    for idx, name in enumerate(ordered_names):
        if name not in priority:
            priority[name] = idx

    def sort_key(provider):
        return (priority.get(provider.get("name"), len(priority)),)

    return sorted(providers, key=sort_key)


def _build_ollama_provider():
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    explicit_mode = os.getenv("OLLAMA_MODE", "").strip().lower()
    native_base_url = os.getenv("OLLAMA_URL", "").strip()
    compat_base_url = os.getenv("OLLAMA_BASE_URL", "").strip()

    use_openai_compat = explicit_mode == "openai" or (compat_base_url and explicit_mode != "native")
    if use_openai_compat:
        base_url = _normalize_base_url(compat_base_url or "https://ollama.com/v1")
        return {
            "name": "ollama",
            "enabled": True,
            "api_mode": "openai",
            "api_key": api_key or None,
            "url": base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions",
            "model": model,
            "max_tokens": 4096,
        }

    base_url = _normalize_base_url(native_base_url or "http://localhost:11434")
    return {
        "name": "ollama",
        "enabled": True,
        "api_mode": "native",
        "api_key": api_key or None,
        "url": base_url if base_url.endswith("/api/chat") else base_url + "/api/chat",
        "model": model,
        "max_tokens": 4096,
    }


_bootstrap_openclaw_ai_env()

PROVIDERS = [
    {
        "name": "claude",
        "enabled": True,
        "api_key_env": "ANTHROPIC_API_KEY",
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
    },
    _build_openai_compat_provider(
        "googleai",
        "OPENCLAW_GOOGLEAI_BASE_URL",
        "OPENCLAW_GOOGLEAI_MODEL",
        "GOOGLE_AI_API_KEY",
        "gemini-2.5-flash",
    ),
    {
        "name": "gemini",
        "enabled": True,
        "api_key_env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "model": os.getenv("OPENCLAW_GOOGLEAI_MODEL", "gemini-2.5-flash"),
        "max_tokens": 4096,
    },
    _build_openai_compat_provider(
        "nvidia",
        "OPENCLAW_NVIDIA_BASE_URL",
        "OPENCLAW_NVIDIA_MODEL",
        "NVIDIA_API_KEY",
        "moonshotai/kimi-k2.5",
    ),
    _build_ollama_provider(),
]
PROVIDERS = _apply_provider_priority(PROVIDERS)

# Timeout em segundos para cada provider
TIMEOUT = 120

# System prompt do Doctor (injetado em todas as chamadas)
DOCTOR_SYSTEM_PROMPT = """Você é o DOCTOR, sub-agente autônomo de diagnóstico do sistema Assistente Verona.
Seu papel é analisar dados de saúde e segurança do sistema e fornecer:
1. Diagnóstico preciso da causa raiz de cada falha
2. Classificação de severidade (CRÍTICA, ALTA, MÉDIA, BAIXA)
3. Decisão se é seguro autocorrigir ou se precisa intervenção humana
4. Comando exato de correção quando autocorrigível
5. Relatório claro e objetivo

REGRAS DE SEGURANÇA:
- NUNCA sugira alterar .env ou credentials diretamente
- NUNCA sugira deletar dados de produção
- NUNCA sugira push para repositório remoto
- Se detectar possível invasão, classifique como CRÍTICA e peça intervenção humana
- Prefira correções conservadoras e reversíveis

Responda SEMPRE em JSON válido conforme o schema solicitado."""


# ══════════════════════════════════════════════════════════════════
# CHAMADAS POR PROVIDER
# ══════════════════════════════════════════════════════════════════

def _call_claude(provider, system_prompt, user_prompt):
    """Chamada à API da Anthropic (Claude)."""
    api_key = os.getenv(provider["api_key_env"], "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não configurada")

    payload = json.dumps({
        "model": provider["model"],
        "max_tokens": provider["max_tokens"],
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode()

    req = urllib.request.Request(
        provider["url"],
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    # Extrair texto da resposta
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    return text


def _call_gemini(provider, system_prompt, user_prompt):
    """Chamada à API do Google Gemini."""
    api_key = os.getenv(provider["api_key_env"], "") or os.getenv("GOOGLE_AI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY/GOOGLE_AI_API_KEY não configurada")

    url = provider["url"].format(model=provider["model"]) + f"?key={api_key}"

    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": provider["max_tokens"],
            "temperature": 0.2,
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    # Extrair texto
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)
    return ""


def _resolve_provider_api_key(provider):
    if provider.get("api_key"):
        return provider["api_key"]
    api_key_env = provider.get("api_key_env")
    if not api_key_env:
        return ""
    return os.getenv(api_key_env, "").strip()


def _call_openai_compatible(provider, system_prompt, user_prompt):
    """Chamada para providers compatíveis com OpenAI Chat Completions."""
    headers = {"Content-Type": "application/json"}
    api_key = _resolve_provider_api_key(provider)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps({
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "max_tokens": provider["max_tokens"],
        "temperature": 0.2,
    }).encode()

    req = urllib.request.Request(
        provider["url"],
        data=payload,
        headers=headers,
        method="POST",
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT * 2, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts)
    return ""


def _call_ollama(provider, system_prompt, user_prompt):
    """Chamada ao Ollama local ou compatível OpenAI em nuvem."""
    if provider.get("api_mode") == "openai":
        return _call_openai_compatible(provider, system_prompt, user_prompt)

    headers = {"Content-Type": "application/json"}
    api_key = _resolve_provider_api_key(provider)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps({
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"num_predict": provider["max_tokens"]},
    }).encode()

    req = urllib.request.Request(
        provider["url"],
        data=payload,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT * 2) as resp:
        data = json.loads(resp.read().decode())

    return data.get("message", {}).get("content", "")


CALLER_MAP = {
    "claude": _call_claude,
    "googleai": _call_openai_compatible,
    "gemini": _call_gemini,
    "nvidia": _call_openai_compatible,
    "ollama": _call_ollama,
}


# ══════════════════════════════════════════════════════════════════
# INTERFACE PRINCIPAL
# ══════════════════════════════════════════════════════════════════

class DoctorBrain:
    """Cérebro de IA do Doctor com fallback entre providers."""

    def __init__(self, extra_system_prompt=""):
        self.system_prompt = DOCTOR_SYSTEM_PROMPT
        if extra_system_prompt:
            self.system_prompt += "\n\n" + extra_system_prompt
        self.last_provider_used = None
        self.last_raw_response = None
        self.last_schema_status = None
        self.errors = []

    def think(self, user_prompt, expect_json=True):
        """
        Envia prompt para IA com fallback automático.
        Retorna (response_text, provider_name) ou (None, None) se todos falharem.
        """
        for provider in PROVIDERS:
            if not provider["enabled"]:
                continue

            name = provider["name"]
            caller = CALLER_MAP.get(name)
            if not caller:
                continue

            try:
                print(f"  [Brain] Tentando {name}...", end=" ")
                response = caller(provider, self.system_prompt, user_prompt)

                if not response or not response.strip():
                    raise ValueError("Resposta vazia")

                self.last_provider_used = name
                self.last_raw_response = response.strip()
                print(f"✅ ({len(response)} chars)")
                return response.strip(), name

            except Exception as e:
                error_msg = f"{name}: {type(e).__name__}: {str(e)[:100]}"
                self.errors.append(error_msg)
                print(f"❌ ({error_msg})")
                continue

        print("  [Brain] ⚠️ Todos os providers falharam!")
        return None, None

    def analyze_findings(self, findings_data, context_data):
        """
        Analisa findings coletados e retorna diagnóstico enriquecido.
        """
        prompt = f"""Analise os seguintes dados de saúde e segurança do sistema Assistente Verona.

## Dados coletados (findings)
```json
{json.dumps(findings_data, indent=2, ensure_ascii=False, default=str)[:6000]}
```

## Contexto do sistema
```json
{json.dumps(context_data, indent=2, ensure_ascii=False, default=str)[:2000]}
```

Responda em JSON com este schema exato:
{{
  "diagnostics": [
    {{
      "finding_index": 0,
      "root_cause": "explicação da causa raiz",
      "severity_adjusted": "CRÍTICA|ALTA|MÉDIA|BAIXA",
      "is_safe_to_autofix": true/false,
      "fix_command": "comando shell exato ou null",
      "fix_explanation": "o que o comando faz",
      "risk_if_ignored": "consequência de não corrigir",
      "related_findings": [indices de findings relacionados]
    }}
  ],
  "overall_health": "OK|DEGRADED|CRITICAL",
  "priority_order": [indices em ordem de prioridade],
  "patterns_detected": ["padrões entre as falhas"],
  "recommendations": ["recomendações gerais"]
}}"""

        response, provider = self.think(prompt)
        if not response:
            self.last_schema_status = "no_response"
            return None, None

        # Extrair JSON da resposta
        parsed = self._extract_json(response)
        normalized = self._normalize_diagnostics_payload(parsed)
        if normalized:
            self.last_schema_status = "ok"
            return normalized, provider

        self.last_schema_status = "invalid_diagnostics_schema"
        return None, provider

    def generate_report_narrative(self, findings, actions, diagnostics):
        """Gera narrativa em linguagem natural para o relatório."""
        prompt = f"""Com base nos dados abaixo, gere uma narrativa concisa (máx 300 palavras) em português
sobre o estado do sistema, principais problemas e ações tomadas.
Tom: técnico mas acessível, direto ao ponto.

Findings: {json.dumps(findings[:10], ensure_ascii=False, default=str)[:3000]}
Ações tomadas: {json.dumps(actions[:10], ensure_ascii=False, default=str)[:2000]}
Diagnóstico IA: {json.dumps(diagnostics, ensure_ascii=False, default=str)[:2000]}

Responda em JSON: {{"narrative": "texto da narrativa", "summary_oneliner": "resumo em 1 linha"}}"""

        response, provider = self.think(prompt)
        if not response:
            return {"narrative": "Relatório gerado sem IA (todos providers indisponíveis).",
                    "summary_oneliner": "Análise manual necessária"}, None

        parsed = self._extract_json(response)
        return parsed or {"narrative": response[:500], "summary_oneliner": "Ver detalhes"}, provider

    def evaluate_security_threat(self, security_findings):
        """Avalia se findings de segurança indicam invasão real."""
        if not security_findings:
            return {"threat_level": "NONE", "is_invasion": False}, None

        prompt = f"""Avalie se os seguintes achados de segurança indicam uma invasão real
ou são operações normais do sistema.

Achados de segurança:
```json
{json.dumps(security_findings, indent=2, ensure_ascii=False, default=str)[:4000]}
```

Responda em JSON:
{{
  "threat_level": "NONE|LOW|MEDIUM|HIGH|CRITICAL",
  "is_invasion": true/false,
  "confidence": 0.0-1.0,
  "evidence": ["evidências que sustentam a conclusão"],
  "immediate_actions": ["ações imediatas se invasão confirmada"],
  "false_positive_reasons": ["razões que podem ser falso positivo"]
}}"""

        response, provider = self.think(prompt)
        if not response:
            return {"threat_level": "UNKNOWN", "is_invasion": False,
                    "confidence": 0, "evidence": ["IA indisponível"]}, None

        parsed = self._extract_json(response)
        return parsed or {"threat_level": "UNKNOWN"}, provider

    def _extract_json(self, text):
        """Extrai JSON de uma resposta que pode conter markdown ou texto extra."""
        # Tentar parse direto
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Tentar extrair bloco ```json ... ```
        import re
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Tentar encontrar { ... } mais externo
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _normalize_diagnostics_payload(self, parsed):
        """Normaliza respostas estruturadas da IA para o schema esperado."""
        if isinstance(parsed, list):
            parsed = {"diagnostics": parsed}
        if not isinstance(parsed, dict):
            return None

        diagnostics = parsed.get("diagnostics")
        if diagnostics is None:
            for alt_key in ("findings", "analysis", "items", "results"):
                alt_value = parsed.get(alt_key)
                if isinstance(alt_value, list):
                    diagnostics = alt_value
                    break

        if diagnostics is None:
            diagnostics = []
        if not isinstance(diagnostics, list):
            return None

        normalized_diagnostics = []
        for idx, diag in enumerate(diagnostics):
            if not isinstance(diag, dict):
                continue

            finding_index = diag.get("finding_index", diag.get("findingIndex", diag.get("index", idx)))
            if not isinstance(finding_index, int):
                try:
                    finding_index = int(finding_index)
                except Exception:
                    finding_index = idx

            related = diag.get("related_findings", diag.get("relatedFindings", []))
            if not isinstance(related, list):
                related = []

            normalized_diagnostics.append({
                "finding_index": finding_index,
                "root_cause": diag.get("root_cause", diag.get("rootCause", diag.get("cause", ""))),
                "severity_adjusted": diag.get(
                    "severity_adjusted",
                    diag.get("severityAdjusted", diag.get("severity"))
                ),
                "is_safe_to_autofix": diag.get(
                    "is_safe_to_autofix",
                    diag.get("isSafeToAutofix", diag.get("safe_to_autofix", False))
                ),
                "fix_command": diag.get("fix_command", diag.get("fixCommand")),
                "fix_explanation": diag.get(
                    "fix_explanation",
                    diag.get("fixExplanation", diag.get("explanation", ""))
                ),
                "risk_if_ignored": diag.get(
                    "risk_if_ignored",
                    diag.get("riskIfIgnored", diag.get("risk", ""))
                ),
                "related_findings": related,
            })

        recommendations = parsed.get("recommendations", [])
        if isinstance(recommendations, str):
            recommendations = [recommendations]
        if not isinstance(recommendations, list):
            recommendations = []

        patterns = parsed.get("patterns_detected", parsed.get("patternsDetected", []))
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            patterns = []

        priority_order = parsed.get("priority_order", parsed.get("priorityOrder", []))
        if not isinstance(priority_order, list):
            priority_order = []

        overall_health = parsed.get("overall_health", parsed.get("overallHealth", "UNKNOWN"))
        if not isinstance(overall_health, str):
            overall_health = "UNKNOWN"

        return {
            "diagnostics": normalized_diagnostics,
            "overall_health": overall_health,
            "priority_order": priority_order,
            "patterns_detected": patterns,
            "recommendations": recommendations,
        }

        return None
