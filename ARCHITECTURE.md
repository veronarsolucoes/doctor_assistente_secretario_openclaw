# Arquitetura do Doctor

## 1. Visao Geral

O `doctor` e um agente local escrito em Python que roda diretamente na VPS, sem container proprio, para observar e corrigir o ambiente do projeto.

Ele combina:

- regras deterministicas
- leitura de estado do host e do Docker
- classificacao assistida por IA
- autocorrecao sob allowlist
- geracao de relatorio e escalacao

## 2. Componentes

### 2.1 `doctor.py`

Responsavel por:

- configuracao
- coleta de contexto
- health checks
- auditoria de seguranca
- autocorrecao
- montagem de relatorio
- escalacao

### 2.2 `doctor_brain.py`

Responsavel por:

- integracao com provider de IA
- diagnostico de causa raiz
- enriquecimento de severidade e risco
- recomendacoes textuais

### 2.3 `run_doctor.sh`

Responsavel por:

- preparar variaveis de ambiente
- apontar diretorios corretos
- executar o `doctor.py`
- manter log principal

## 3. Pipeline de Execucao

O fluxo interno atual segue estas fases:

1. `Fase 0`
   coleta logs persistidos e historico recente
2. `Fase 1`
   health check de Docker, servicos, API, disco, memoria, build cache e zumbis
3. `Fase 2`
   auditoria de seguranca e integridade
4. `Fase 3`
   classificacao, recorrencia e diagnostico com IA
5. `Fase 4`
   aplicacao de autocorrecoes seguras
6. `Fase 5`
   geracao de relatorio markdown/json
7. `Fase 6`
   escalacao e compartilhamento de estado

## 4. Fontes de Estado

O `doctor` depende principalmente de:

- `docker compose`
- `docker inspect`
- logs de servicos Docker
- sistema de arquivos do repositorio
- arquivos internos em `state/`
- endpoints HTTP locais da API

## 5. Diretorios Principais

- runtime:
  - [doctor_ribeiro](/root/doctor_ribeiro)
- relatorios:
  - [reports](/root/doctor_ribeiro/reports)
- logs:
  - [logs](/root/doctor_ribeiro/logs)
- estado:
  - [state](/root/doctor_ribeiro/state)

## 6. Principios de Projeto

- priorizar deteccao local antes de inferencia por IA
- limitar autocorrecao a comandos seguros e verificaveis
- escalar em vez de improvisar manutencao invasiva
- manter relatorio explicavel e reproduzivel
