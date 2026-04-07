# Changelog do Doctor

Registro tecnico das alteracoes aplicadas ao comportamento do `doctor`.

## 2026-04-07 00:17 - Reinicio autonomo de servicos Docker

Arquivos:
- `doctor.py`

Alteracoes:
- adicionado caminho seguro de `docker compose restart <servico>` para servicos monitorados
- diferenciacao entre:
  - servico ausente: `docker compose up -d <servico>`
  - servico existente com falha: `docker compose restart <servico>`
- verificacao pos-correcao para garantir:
  - `state=running`
  - `health=healthy` quando houver healthcheck
- smoke tests da API agora usam recuperacao inteligente do servico `api`

Objetivo:
- permitir que o Doctor recupere containers `restarting` ou `unhealthy` sem depender sempre de `up -d`

## 2026-04-07 00:00 - Causa raiz local para duplicidade em event_log

Arquivos:
- `doctor.py`

Alteracoes:
- deteccao local do padrao `duplicate key ... event_log`
- relatorio passou a registrar `Causa Raiz` com fallback local
- finding reflexo da `api` pode herdar causa raiz local do problema em `secretario.event_log`

Objetivo:
- reduzir dependencia exclusiva da IA para explicar o bug de idempotencia do `EventLog`

## 2026-04-06 23:36 - Smoke tests HTTP e blindagem de credentials

Arquivos:
- `doctor.py`

Alteracoes:
- smoke tests:
  - `GET /health`
  - `GET /api/v1/webhooks/whatsapp/health`
- tentativa de recuperacao da API quando os smoke tests falham
- tratamento benigno para `?? codigo/credentials/` quando o Git ja esta configurado corretamente
- auditoria e autocorrecao de permissao para arquivos em `codigo/credentials/`

Objetivo:
- validar funcionamento basico da aplicacao
- reduzir falso positivo de `git-uncommitted`
- endurecer permissao de credenciais locais

## 2026-04-06 23:12 - Limpeza autonoma de build cache e zumbis

Arquivos:
- `doctor.py`

Alteracoes:
- deteccao de build cache do Docker
- autocorrecao com `docker builder prune -af`
- deteccao de processos zumbi
- tentativa de reap com `kill -CHLD <ppid>`
- allowlist expandida para:
  - `docker builder prune -af`
  - `kill -CHLD <pid>`
  - `chmod 700 <path>`

Objetivo:
- permitir saneamento autonomo de lixo operacional seguro no host
