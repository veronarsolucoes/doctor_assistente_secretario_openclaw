# Autocorrecoes do Doctor

## 1. Filosofia

O `doctor` so executa autocorrecoes:

- previsiveis
- reversiveis ou de baixo risco
- limitadas a uma allowlist
- verificaveis apos a execucao

## 2. Comandos Permitidos

Atualmente, a allowlist cobre:

- `docker image prune -f`
- `docker builder prune -af`
- `docker compose up -d <servico_monitorado>`
- `docker compose restart <servico_monitorado>`
- `kill -CHLD <pid>`
- `chmod 600 <path>`
- `chmod 700 <path>`

## 3. Casos Cobertos

### 3.1 Docker

- servico ausente do compose
- container existente em falha
- container `unhealthy`
- cache de build crescendo desnecessariamente

### 3.2 Sistema

- processos zumbi com pai identificavel

### 3.3 Seguranca Basica

- permissao insegura em arquivos sensiveis dentro do escopo monitorado

## 4. Casos Nao Cobertos

- rebuild de imagem
- alteracao de Dockerfile
- ajuste destrutivo em volumes
- remocao de containers ou imagens especificas
- refatoracao de codigo da aplicacao
- mudanca automatica de secrets

## 5. Verificacao Pos-Fix

O `doctor` tenta validar o efeito da correcao:

- `restart/up -d`:
  - `running`
  - `healthy`, quando existir healthcheck
- `docker builder prune -af`:
  - cache restante zerado
- `kill -CHLD`:
  - ausencia de zumbis sob o mesmo `ppid`

Se a verificacao falhar:

- a correcao nao e marcada como efetiva
- o item continua elegivel para intervencao humana
