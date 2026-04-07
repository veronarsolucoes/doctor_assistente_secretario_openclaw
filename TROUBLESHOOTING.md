# Troubleshooting do Doctor

## 1. Relatorio mostra erro historico que ja foi corrigido

Causa comum:

- o `doctor` lê erros desde o start atual do container
- se o container nao foi reiniciado, erros antigos continuam visiveis

Acao:

- reiniciar o servico afetado
- rodar o `doctor` novamente

## 2. Servico continua falhando mesmo apos restart automatico

Causas comuns:

- permissao incorreta em bind mount
- owner incorreto em volume
- imagem quebrada
- bug de aplicacao

Acao:

- inspecionar `docker logs`
- validar mounts e owners
- verificar se precisa `up -d --build`

## 3. API esta healthy, mas ainda ha alertas de banco

Causa comum:

- o problema atual pode ter sido corrigido, mas o `postgres` ainda expõe erro historico na janela de logs

Acao:

- validar a correcao real
- reiniciar o `postgres` se o objetivo for zerar a janela do container

## 4. Celery falha apos rebuild

Causa comum:

- imagem passou a rodar com usuario nao-root
- bind mount de logs/dados nao permite escrita

Acao:

- descobrir `uid/gid` do usuario do container
- alinhar owner/permissoes no host

## 5. O Doctor nao corrige um item que parece simples

Motivos comuns:

- comando fora da allowlist
- correcao nao e segura o bastante
- caso exige rebuild, remocao ou alteracao destrutiva

Acao:

- consultar [AUTOCORRECOES.md](/root/doctor_ribeiro/AUTOCORRECOES.md)
- aplicar correcao manual
- se fizer sentido, ensinar nova regra segura ao `doctor`
