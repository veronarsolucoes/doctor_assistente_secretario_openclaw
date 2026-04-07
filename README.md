# Doctor

Subagente operacional para monitoramento, diagnostico e autocorrecao segura do ambiente do Assistente Secretario Ribeiro.

## 1. O que e

O `doctor` roda direto na VPS e executa um ciclo composto por:

- coleta de contexto e historico
- health check de infraestrutura e aplicacao
- auditoria basica de seguranca e integridade
- classificacao e enriquecimento com IA
- autocorrecao dentro de uma allowlist
- geracao de relatorio
- escalacao quando necessario

## 2. Estrutura Principal

- [doctor.py](/root/doctor_ribeiro/doctor.py)
- [doctor_brain.py](/root/doctor_ribeiro/doctor_brain.py)
- [run_doctor.sh](/root/doctor_ribeiro/run_doctor.sh)
- [reports](/root/doctor_ribeiro/reports)
- [logs](/root/doctor_ribeiro/logs)
- [state](/root/doctor_ribeiro/state)

## 3. Como Executar

Execucao manual:

```bash
/root/doctor_ribeiro/run_doctor.sh
```

Saidas principais:

- relatorios markdown/json em [reports](/root/doctor_ribeiro/reports)
- log principal em [doctor.log](/root/doctor_ribeiro/logs/doctor.log)
- memoria e estado compartilhado em [state](/root/doctor_ribeiro/state)

## 4. Configuracao

Variaveis operacionais principais definidas em [run_doctor.sh](/root/doctor_ribeiro/run_doctor.sh):

- `DOCTOR_DIR`
- `DOCTOR_REPO_DIR`
- `DOCTOR_CODEBASE_DIR`
- `DOCTOR_COMPOSE_FILE`
- `DOCTOR_MONITORED_SERVICES`
- `DOCTOR_OPTIONAL_SERVICES`
- `DOCTOR_AI_PRIMARY_PROVIDER`

Valor padrao atual de `DOCTOR_MONITORED_SERVICES`:

```text
postgres,redis,api,celery-worker,celery-beat
```

## 5. Documentacao

1. Arquitetura:
   [ARCHITECTURE.md](/root/doctor_ribeiro/ARCHITECTURE.md)
2. Operacao e execucao:
   [OPERACAO.md](/root/doctor_ribeiro/OPERACAO.md)
3. Autocorrecoes e allowlist:
   [AUTOCORRECOES.md](/root/doctor_ribeiro/AUTOCORRECOES.md)
4. Troubleshooting:
   [TROUBLESHOOTING.md](/root/doctor_ribeiro/TROUBLESHOOTING.md)
5. Restart autonomo Docker:
   [DOC_RESTART_DOCKER_AUTONOMO.md](/root/doctor_ribeiro/DOC_RESTART_DOCKER_AUTONOMO.md)

## 6. Historico Tecnico

- [CHANGELOG_DOCTOR.md](/root/doctor_ribeiro/CHANGELOG_DOCTOR.md)

## 7. Limites de Projeto

- o `doctor` nao faz rebuild automatico de imagem
- o `doctor` nao executa comandos destrutivos fora da allowlist
- o `doctor` nao corrige bugs de negocio sozinho
- o `doctor` pode escalar achados para intervencao humana quando a correcao nao for segura
