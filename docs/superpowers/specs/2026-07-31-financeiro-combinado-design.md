# Financeiro Hoje e Medição na mesma automação

## Objetivo

Entregar uma única imagem versionada de `dmais_automacoes` que preserve o
Financeiro Hoje e acrescente a Medição LOGA 11–10, sem mover a execução de RPA
para o Django e sem alterar as publicações já gravadas no volume compartilhado.

## Causa da regressão

O Financeiro Hoje existia na imagem de produção
`financeiro-retry-4c31dd8`, mas não estava no `origin/main` usado para montar a
imagem da Medição. O deploy substituiu o artefato inteiro; a flag
`FINANCEIRO_HOJE_SCHEDULE_ENABLED=true` permaneceu, porém o módulo e seu
registro no worker não existiam na nova imagem.

## Limites de responsabilidade

- `dmais_automacoes` possui navegador, credenciais, horários, sinais e locks.
- O volume `/app/data_pipeline` contém pacotes publicados e estado operacional.
- O Django apenas resolve ponteiros imutáveis, valida pacotes e exibe status.
- Cada fluxo mantém root próprio: `financeiro_hoje` e `financeiro_medicao`.
- Nenhum arquivo já publicado é convertido, renomeado ou sobrescrito por esta
  correção.

## Integração do worker

O worker atual da Medição continua sendo a base. O Financeiro Hoje entra como
mais uma capacidade independente:

- opt-in por `FINANCEIRO_HOJE_SCHEDULE_ENABLED`;
- agenda exata no fuso configurado;
- sinal manual preservado em falha, lock ou execução já ativa;
- evento registrado junto de RouterBox, Multiplica, Protheus e Medição;
- prioridade determinística em empates;
- avanço do slot somente depois da tentativa correspondente.

O processo deve registrar no startup o estado das duas agendas. Uma imagem com
flag ativa e módulo ausente deve falhar no smoke test de build, em vez de subir
silenciosamente incompleta.

## Concorrência

O worker é síncrono e despacha um evento por vez. A coordenação já validada do
Financeiro Hoje com RouterBox será preservada nesta correção emergencial. O
lock global de Chromium da Medição, Multiplica, Protheus e RouterBox permanece.
A migração do Financeiro Hoje para o lock global será feita somente com teste
dedicado, sem remover seu deadline absoluto ou o bloqueio de proximidade do
RouterBox.

## Segurança e dados

- Segredos continuam somente no ambiente/arquivos de segredo.
- O `.env.example` recebe apenas nomes e valores não secretos.
- O código recuperado será comparado por SHA-256 com a imagem funcional.
- A imagem será identificada por tag imutável e revisão Git.
- O bind `/srv/dmais/data_pipeline:/app/data_pipeline` não muda no deploy.

## Validação

1. Testes originais do Financeiro Hoje passam após recuperação do artefato.
2. Um teste de regressão falha enquanto o worker não registrar o Financeiro
   Hoje ao lado da Medição.
3. Testes focados cobrem agenda, sinal, avanço de evento e coexistência.
4. A suíte combinada roda uma vez antes do build.
5. O container faz smoke importando os dois módulos.
6. Em produção, uma coleta real do Financeiro Hoje publica LOGA e ACERTA.
7. O catálogo atual da Medição permanece idêntico e a próxima execução fica em
   00:01 no fuso de São Paulo.

## Retorno seguro

O rollback altera somente a imagem do serviço de automações. O portal e o
volume permanecem montados. Até existir uma imagem combinada anterior, o
retorno preserva dados, mas oferece apenas uma das duas agendas; por isso o
deploy definitivo exige smoke test e verificação de ambas antes da conclusão.
