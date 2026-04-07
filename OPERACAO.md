# Operacao do Doctor

## 1. Execucao Manual

```bash
/root/doctor_ribeiro/run_doctor.sh
```

## 2. Execucao Agendada

Cron atualmente usado no host:

```text
50 4 * * * /root/doctor_ribeiro/run_doctor.sh
```

## 3. Entradas de Configuracao

As principais variaveis operacionais sao definidas em [run_doctor.sh](/root/doctor_ribeiro/run_doctor.sh):

- `DOCTOR_DIR`
- `DOCTOR_REPO_DIR`
- `DOCTOR_CODEBASE_DIR`
- `DOCTOR_COMPOSE_FILE`
- `DOCTOR_MONITORED_SERVICES`
- `DOCTOR_OPTIONAL_SERVICES`
- `DOCTOR_AI_PRIMARY_PROVIDER`

## 4. Saidas

### 4.1 Relatorios

- markdown:
  - [reports](/root/doctor_ribeiro/reports)
- json:
  - [reports](/root/doctor_ribeiro/reports)

### 4.2 Logs

- log principal:
  - [doctor.log](/root/doctor_ribeiro/logs/doctor.log)

### 4.3 Estado

- memoria:
  - [state](/root/doctor_ribeiro/state)
- notificacao pendente:
  - [pending_notification.json](/root/doctor_ribeiro/state/pending_notification.json)

## 5. O que o Ciclo Verifica

- disponibilidade do Docker e do compose
- status dos servicos monitorados
- health HTTP da API
- erros recentes em logs
- uso de disco e memoria
- imagem/cache Docker
- integridade local de codigo
- tokens expostos e permissoes sensiveis
- processos zumbi

## 6. Leitura Basica do Relatorio

Campos principais do resumo:

- `Falhas encontradas`
- `Correcoes aplicadas`
- `Pendentes intervencao humana`
- `Severidade maxima`

Interpretacao pratica:

- `CRITICA`
  indisponibilidade forte ou risco severo
- `ALTA`
  falha importante que ainda exige acao
- `MEDIA`
  problema real, mas sem impacto maximo imediato
- `BAIXA`
  ruido controlado ou manutencao preventiva
