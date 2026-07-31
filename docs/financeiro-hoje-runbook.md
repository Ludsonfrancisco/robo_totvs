# Runbook — Financeiro Hoje

Este procedimento opera a coleta **Financeiro Hoje** no root resolvido por
`FINANCEIRO_HOJE_ROOT`. Sem override, ele é
`$DATA_PIPELINE_DIR/financeiro_hoje` (em produção, normalmente
`/app/data_pipeline/financeiro_hoje`). A agenda é opt-in: mantenha
`FINANCEIRO_HOJE_SCHEDULE_ENABLED=false` até concluir a validação real descrita
neste documento.

Um `FINANCEIRO_HOJE_ROOT` customizado move como uma unidade o sinal manual,
`done.json`, lock exclusivo, runs, evidências, logs, ponteiro e publicações.
Use sempre um caminho absoluto terminado em `financeiro_hoje`. O lock
compartilhado `routerbox-site.lock` fica no **diretório pai** desse root. Para
manter exclusão mútua com o Backlog, esse pai deve ser o mesmo
`DATA_PIPELINE_DIR` usado pelo Backlog RouterBox.

O fluxo coleta e publica **LOGA e ACERTA juntas**. Uma falha em qualquer uma
impede uma nova publicação e preserva o pacote apontado por `current.json`.
Este runbook não altera Backlog RouterBox, Multiplica ou o robô legado.

A coleta Financeiro Hoje em producao exige Linux/POSIX e execucao do worker
no thread principal. Essa combinacao fornece o deadline forte que interrompe
`Download.save_as()` com `SIGALRM`. Em Windows ou thread secundaria, a coleta
falha antes de iniciar o salvamento com
`code: "DOWNLOAD_DEADLINE_UNSUPPORTED"`; ela nao continua sem limite nem
mantem contexto, browser ou locks abertos.

## Agenda aprovada

Com `FINANCEIRO_HOJE_SCHEDULE_ENABLED=true`, o worker agenda os 27 horários
abaixo todos os dias no fuso `FINANCEIRO_HOJE_TIMEZONE` (padrão
`America/Sao_Paulo`). O scheduler converte o relógio do host para esse fuso;
portanto os slots continuam sendo interpretados no fuso configurado mesmo se
o host estiver em outro. São horários exatos, não um intervalo.

| | | | | | | | | |
|---|---|---|---|---|---|---|---|---|
| 00:30 | 06:20 | 07:10 | 07:50 | 08:20 | 08:50 | 09:20 | 09:50 | 10:20 |
| 10:50 | 11:20 | 11:50 | 12:20 | 12:50 | 13:20 | 13:50 | 14:20 | 14:50 |
| 15:20 | 16:10 | 17:10 | 18:10 | 19:10 | 20:10 | 21:10 | 22:00 | 23:00 |

Em empate, Backlog RouterBox tem prioridade. O runner Financeiro Hoje espera o
lock compartilhado `routerbox-site.lock` até seu prazo absoluto (480 segundos
por padrão); se não o obtiver, registra `stage: "lock"` e
`code: "ROUTERBOX_SITE_BUSY"`.

## Gate de ativação

Não habilite a agenda apenas porque os testes locais passaram. Antes de mudar
`FINANCEIRO_HOJE_SCHEDULE_ENABLED` para `true`, é obrigatório validar em live
pelo sinal manual, com credenciais reais, e confirmar no mesmo `run_id` que
**LOGA e ACERTA** foram baixadas, validadas e publicadas juntas. A aprovação
exige em `done.json`: `success: true`, `stage: "published"`,
`message: "LOGA e ACERTA publicadas."` e `alert_active: false`; `current.json`
deve apontar para esse `run_id`.

Se qualquer fonte falhar, se o prazo vencer, se o lock não for obtido ou se a
publicação não for confirmada, mantenha a flag em `false`, corrija a causa e
repita a validação manual. Não use o primeiro slot agendado como teste live.

## Acionamento manual

O sinal real é `$FINANCEIRO_HOJE_ROOT/financeiro_hoje.signal` (sem override,
`$DATA_PIPELINE_DIR/financeiro_hoje/financeiro_hoje.signal`). Ele é aceito mesmo
com `FINANCEIRO_HOJE_SCHEDULE_ENABLED=false`.

O conteúdo é um objeto JSON com `scheduled_for` ISO 8601. Use o timestamp
corrente do fuso da operação e inclua o offset, por exemplo:

```json
{"scheduled_for":"2026-07-28T08:20:00-03:00"}
```

Crie o arquivo somente se ele ainda não existir; não substitua um pedido manual
pendente. O worker faz polling em até 5 segundos por padrão. Depois de validar
o JSON e o timestamp, e somente se o Backlog RouterBox não estiver a 90
segundos ou menos, ele remove o sinal imediatamente antes de chamar o runner.
Sinal inválido é preservado para correção. Se o runner lançar exceção, o worker
recria o payload original, sem sobrescrever um sinal novo criado no intervalo.

Um horário no passado pode exceder o prazo de 480 segundos imediatamente: para
a validação use um `scheduled_for` recém-gerado e crie o sinal sem demora. O
sinal não aciona Backlog nem Multiplica.

## Estado, resultado e evidências

O resultado Financeiro Hoje é `$FINANCEIRO_HOJE_ROOT/done.json` (sem override,
`$DATA_PIPELINE_DIR/financeiro_hoje/done.json`). Não existe nem deve ser
consultado `run_financeiro_hoje.done`: `run_routerbox.done` é do Backlog e
`run.done` é do robô legado. O worker não remove `done.json` no início de uma
tentativa; o último resultado permanece até substituição atômica.

| Campo | Interpretação operacional |
|---|---|
| `success` / `stage` / `code` | Resultado e ponto da falha. Sucesso é `published`; falhas típicas: `download`, `validation`, `consolidation`, `publication`, `deadline` (`DEADLINE_EXCEEDED`) e `lock` (`ROUTERBOX_SITE_BUSY`). |
| `company` | `LOGA` ou `ACERTA` quando a falha é de uma fonte; `null` para lock, prazo ou consolidação. |
| `alert_active` / `last_success` / `recovered_at` | Falha ativa alerta e preserva o último sucesso. Após sucesso posterior, baixa o alerta e registra a recuperação. |
| `scheduled_for`, `started_at`, `finished_at`, `next_scheduled_for` | Linha do tempo em ISO 8601 com offset e próximo slot calculado. |
| `run_id` | Identificador do conjunto de artefatos. |
| `state_skipped: "ACTIVE_RUN"` | Campo retornado pelo `run_once` quando outro ciclo tem o lock exclusivo. Não é gravado em `done.json` nem em `logs/history.jsonl`; não o trate como nova evidência persistida. |
| `state_persisted`, `state_error`, `housekeeping` | Diagnósticos retornados pelo `run_once`, também não persistidos no `done.json`. `STATE_PERSIST_FAILED` ou `HOUSEKEEPING_FAILED` requer investigação, mesmo se o pacote já foi publicado. |

Para um `run_id`, procure:

```text
<FINANCEIRO_HOJE_ROOT>/
├── runs/<run_id>/
│   ├── original_loga.xlsx
│   ├── original_acerta.xlsx
│   └── consolidado.xlsx
├── published/<run_id>/
│   ├── consolidado.xlsx
│   └── manifest.json
├── current.json                         # ponteiro do pacote publicado
├── evidence/<run_id>/loga/<codigo>.png  # screenshot de falha; inputs mascarados
├── evidence/<run_id>/acerta/<codigo>.png
├── logs/history.jsonl                   # uma linha JSON por resultado persistido
└── done.json                            # último resultado Financeiro Hoje
```

`manifest.json` contém hash, tamanho e linhas de LOGA, ACERTA e consolidado;
confira-o com o `current.json` antes de distribuir um pacote. Screenshots são
gravados somente em falha de coleta e mascaram os inputs da página.

Retenção automática, após cada resultado persistido:

- `runs/`: 7 dias, exceto o diretório do `current_run_id`, preservado enquanto
  continuar corrente;
- `published/`: 7 dias, preservando sempre o pacote do `current_run_id` e ao
  menos os 3 pacotes mais recentes;
- `evidence/`: 14 dias, exceto o diretório do `current_run_id`, preservado
  enquanto continuar corrente;
- `logs/history.jsonl`: 30 dias, conforme timestamps válidos do histórico.

Não limpe manualmente durante investigação. Copie evidência necessária para
retenção externa antes do vencimento, sem alterar artefatos gerenciados.

## Locks e recuperação segura

Há dois locks:

- `$FINANCEIRO_HOJE_ROOT/financeiro_hoje.lock` é exclusivo do ciclo Financeiro Hoje,
  contém token aleatório e é removido pelo próprio dono ao sair.
- `$FINANCEIRO_HOJE_ROOT/../routerbox-site.lock` é compartilhado com o Backlog.
  No layout padrão, equivale a `$DATA_PIPELINE_DIR/routerbox-site.lock`. O
  runner o adquire e libera; nunca o remova como recuperação do Financeiro
  Hoje.

`FINANCEIRO_HOJE_BUSY` indica que outra tentativa tem o lock exclusivo. Espere
e consulte `done.json`/`history.jsonl` antes de intervir. A remoção manual de
`financeiro_hoje.lock` é somente para lock órfão e respeita esta ordem:

1. Desabilite a agenda Financeiro Hoje (`FINANCEIRO_HOJE_SCHEDULE_ENABLED=false`)
   e impeça novos sinais manuais.
2. Em **cada** réplica/container que monta o volume, confirme ausência de
   processo `python worker.py` e de qualquer execução manual que invoque
   `flows.financeiro_hoje.runner`.
3. Confirme pelos logs que não há tentativa ativa e que `done.json` não está
   sendo atualizado; registre ambiente, horário e motivo da decisão.
4. Somente então remova **apenas**
   `$FINANCEIRO_HOJE_ROOT/financeiro_hoje.lock`. Não toque em
   `$FINANCEIRO_HOJE_ROOT/../routerbox-site.lock`,
   `current.json`, `published/`, Backlog ou Multiplica.
5. Repita a validação manual LOGA+ACERTA antes de reconsiderar a flag.

Se não for possível comprovar ausência de processo, não remova o lock; escale
a ocorrência. Apagar lock de processo vivo permite duas coletas no mesmo site.

## Habilitar, desabilitar e rollback isolado

Após o gate live aprovado, habilite no serviço:

```text
FINANCEIRO_HOJE_SCHEDULE_ENABLED=true
```

Confirme no log o próximo evento `financeiro_hoje` no slot esperado e acompanhe
o primeiro resultado. Para desabilitar, retorne a flag a `false` e aplique a
configuração do serviço: isso remove apenas Financeiro Hoje da agenda; o sinal
manual continua disponível enquanto o worker estiver rodando.

No incidente, rollback isolado significa manter a flag em `false`, preservar
`done.json`, `current.json`, pacote publicado, histórico e evidências, e parar
somente novos disparos Financeiro Hoje. A flag desliga a agenda, mas **não**
desliga o consumo do sinal manual. Trate sinal pendente nesta ordem:

1. Bloqueie primeiro o produtor que cria
   `$FINANCEIRO_HOJE_ROOT/financeiro_hoje.signal`.
2. Defina `FINANCEIRO_HOJE_SCHEDULE_ENABLED=false` e aplique a configuração.
   A mudança operacional exige checkpoint com horário, ambiente, responsável,
   root resolvido e réplicas afetadas.
3. Pelo mecanismo do serviço, pare ou congele **todas** as réplicas do worker
   que podem consumir esse root. Essa etapa é obrigatória antes de inspecionar
   ou mover o sinal. Como o worker é compartilhado, a parada interrompe
   temporariamente também os eventos de Backlog e Multiplica; execute-a em
   janela controlada e autorizada. Não altere flags, produtores ou artefatos
   desses dois fluxos.
4. Confirme em cada réplica que não há processo `worker.py`, processo que
   invoque `flows.financeiro_hoje.runner`, execução Financeiro Hoje ativa nem
   consumidor capaz de retirar o sinal. Registre a confirmação no checkpoint.
   Somente agora inspecione existência e conteúdo do sinal, lock, logs e
   timestamps de `done.json`.
5. Se houver sinal pendente e nenhuma execução ativa, crie `quarantine/` dentro
   do root se necessário, com as mesmas permissões restritas, e renomeie o
   sinal atomicamente, no mesmo filesystem, para
   `$FINANCEIRO_HOJE_ROOT/quarantine/financeiro_hoje.signal.<timestamp>.json`.
   Preserve conteúdo, horário, motivo e responsável como trilha de auditoria;
   nunca simplesmente delete o pedido.
6. Confirme que o caminho ativo não contém sinal e finalize o checkpoint.
   Somente depois restaure as réplicas do worker, mantendo
   `FINANCEIRO_HOJE_SCHEDULE_ENABLED=false` e o produtor do sinal bloqueado.
   O retorno do worker restabelece os eventos Backlog/Multiplica sem mudar suas
   configurações ou arquivos.

Se não for possível parar ou congelar todas as réplicas consumidoras, não
inspecione nem renomeie o sinal: preserve-o e **não declare o rollback
concluído**. Escale a janela de parada. Como alternativa à quarentena, o sinal
pode permanecer no caminho ativo somente enquanto todos os consumidores
continuarem parados; o worker não pode ser restaurado até haver uma disposição
segura para esse pedido.

Não remova locks nem estado como parte desse procedimento; lock órfão segue o
processo separado de recuperação segura acima.

Não altere
`ROUTERBOX_HOURLY_ENABLED`, parâmetros/arquivos do Backlog,
`MULTIPLICA_SCHEDULE_ENABLED`, Multiplica ou o robô legado. Não exclua
serviços, volumes ou arquivos no rollback.

## Checklist do primeiro dia

- [ ] Flag começa em `false`; o `FINANCEIRO_HOJE_ROOT` resolvido é persistente
  e gravável pelo worker, e seu pai contém o `routerbox-site.lock` coordenado
  com o Backlog, sem credenciais em arquivos ou logs.
- [ ] LOGA e ACERTA têm conectividade e credenciais válidas; a janela escolhida
  não conflita com Backlog RouterBox.
- [ ] Foi criado um único sinal válido, com `scheduled_for` atual e offset, sem
  substituir sinal preexistente.
- [ ] A validação live acabou com `success=true`, `stage=published` e
  `alert_active=false`; LOGA, ACERTA e consolidado estão no mesmo `run_id`, e
  `current.json`/`manifest.json` foram conferidos.
- [ ] `done.json` e `logs/history.jsonl` registram o mesmo resultado. Se a
  chamada direta ao runner expuser `STATE_PERSIST_FAILED` ou
  `HOUSEKEEPING_FAILED`, a ativação foi interrompida e a causa foi investigada.
- [ ] Retenção e cópia externa de evidências foram comunicadas ao responsável.
- [ ] Só então a flag foi ligada e o primeiro slot foi monitorado até publicar
  ou alertar.
- [ ] Em falha, produtor e agenda foram bloqueados; todas as réplicas
  consumidoras foram paradas/congeladas e registradas em checkpoint antes de
  inspecionar ou mover o sinal. O worker só voltou com flag `false` e produtor
  bloqueado depois da quarentena auditável. Backlog e Multiplica tiveram apenas
  a interrupção temporária da janela, sem mudanças de configuração ou arquivo.
