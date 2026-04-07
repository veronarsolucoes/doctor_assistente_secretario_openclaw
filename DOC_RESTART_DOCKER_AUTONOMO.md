# Documentacao Tecnica - Reinicio Autonomo de Servicos Docker no Doctor

## Objetivo

Esta funcionalidade permite que o `doctor` recupere automaticamente servicos Docker Compose monitorados quando detectar:

- servico parado
- container em `restarting`
- container em `running` mas com `health=unhealthy`
- falha de smoke test HTTP da API

O objetivo e reduzir intervencao manual em falhas operacionais simples, mantendo uma allowlist estrita de comandos.

## Escopo

A funcionalidade atua apenas sobre os servicos definidos em `DOCTOR_MONITORED_SERVICES`.

Valor padrao atual em [run_doctor.sh](/root/doctor_ribeiro/run_doctor.sh):

- `postgres`
- `redis`
- `api`
- `celery-worker`
- `celery-beat`

Servicos fora dessa lista nao entram no fluxo de autocorrecao.

## Arquivos Envolvidos

- [doctor.py](/root/doctor_ribeiro/doctor.py)
- [run_doctor.sh](/root/doctor_ribeiro/run_doctor.sh)

Replica tecnica mantida em:

- [doctor.py](/root/docker_aisha/doctor.py)
- [CHANGELOG_DOCTOR.md](/root/doctor_ribeiro/CHANGELOG_DOCTOR.md)

## Componentes Implementados

### 1. `get_service_runtime_status(service)`

Consulta o container associado ao servico e retorna:

- `exists`
- `container_id`
- `state`
- `health`

Fonte dos dados:

- `docker compose ps -q <service>`
- `docker inspect <container_id>`

Uso:

- distinguir servico ausente de servico existente em falha
- detectar `unhealthy` mesmo quando o container ainda aparece como `running`

### 2. `compose_restart_cmd(service)`

Gera o comando:

```bash
docker compose -f <compose> restart <service>
```

Sempre executado no diretório do compose monitorado.

### 3. `get_service_recovery_cmd(service)`

Decide o comando de recuperacao com base no estado real do servico:

- se o container existe:
  - usa `docker compose restart <service>`
- se o container nao existe:
  - usa `docker compose up -d <service>`

Essa decisao evita usar `up -d` para todos os casos e melhora a recuperacao de containers em estado ruim.

## Fluxo de Decisao

### Caso 1. Servico nao aparece como running

Durante a Fase 1, o `doctor` consulta:

```bash
docker compose ps --services --status running
```

Se o servico monitorado nao estiver na lista:

- consulta o estado real por `docker inspect`
- decide entre:
  - `restart`
  - `up -d`

Finding gerado:

- componente: nome do servico
- tipo: `INFRAESTRUTURA`
- severidade:
  - `CRITICA` para obrigatorios
  - `MEDIA` para opcionais

### Caso 2. Servico esta running, mas unhealthy

Se o container existir com:

- `state=running`
- `health=unhealthy`

O `doctor` gera finding especifico e tenta:

```bash
docker compose restart <service>
```

Finding gerado:

- tipo: `INFRAESTRUTURA`
- severidade:
  - `ALTA` para obrigatorios
  - `MEDIA` para opcionais

### Caso 3. Smoke test da API falha

Os smoke tests atuais sao:

- `GET /health`
- `GET /api/v1/webhooks/whatsapp/health`

Se houver falha HTTP ou de conectividade:

- o `doctor` usa `get_service_recovery_cmd("api")`
- isso permite:
  - `restart api` quando o container existe
  - `up -d api` quando ele nao existe

## Allowlist de Seguranca

Os comandos aceitos pela autocorrecao agora incluem:

- `docker image prune -f`
- `docker builder prune -af`
- `docker compose up -d <servico_monitorado>`
- `docker compose restart <servico_monitorado>`
- `kill -CHLD <pid>`
- `chmod 600 <path>`
- `chmod 700 <path>`

Restricoes:

- `restart` so e aceito para servicos presentes em `DOCTOR_MONITORED_SERVICES`
- nao existe `restart` arbitrario de qualquer container
- nao existe `docker rm`, `docker kill`, `docker system prune` ou rebuild automatico

## Verificacao Pos-Correcao

Depois de executar `restart` ou `up -d`, o `doctor` valida:

- `state == running`
- `health == healthy` quando houver healthcheck

Se a verificacao falhar:

- a acao nao e considerada efetiva
- o finding continua para intervencao humana

Resumo da regra:

```text
running + healthy => OK
running + sem healthcheck => OK
qualquer outro estado => falha na correcao
```

## Comportamento Esperado

### Recupera automaticamente

- container ausente do compose
- container em `restarting`
- container com `health=unhealthy`
- API indisponivel em smoke test, quando a recuperacao e resolvida por restart do servico

### Nao recupera automaticamente

- bugs de codigo da aplicacao
- falhas de banco por logica incorreta
- rebuild de imagem
- problemas de permissao fora da allowlist
- mudancas em compose, Dockerfile ou volumes

## Limitacoes Conhecidas

- `docker compose restart` nao corrige problema de imagem quebrada
- `restart` nao corrige volume com owner/permissao incorreta
- `restart` nao limpa erro historico ja presente na janela de logs
- o `doctor` nao faz `docker compose up -d --build`
- o `doctor` nao faz correcoes destrutivas ou irreversiveis

## Observacoes Operacionais

Durante a validacao real, a funcionalidade se mostrou adequada para falhas de estado do container, mas nao substitui manutencao em casos como:

- bind mounts com permissao errada
- volume de dados com owner incorreto
- erros de aplicacao que exigem rebuild ou patch de codigo

Nesses casos, o `doctor` consegue reiniciar, detectar reincidencia e escalar corretamente, mas nao deve tentar "adivinhar" manutencoes invasivas.

## Resultado Pratico

Com essa funcionalidade, o `doctor` passou a:

- ser mais preciso na recuperacao de servicos Docker
- evitar `up -d` indiscriminado
- distinguir falha de presenca, falha de runtime e falha de healthcheck
- validar a efetividade da recuperacao antes de marcar sucesso
