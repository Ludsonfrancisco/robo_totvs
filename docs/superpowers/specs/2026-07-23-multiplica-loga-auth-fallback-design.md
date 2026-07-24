# Multiplica Loga — fallback de autenticação

## Objetivo

Manter a coleta do Multiplica operando quando a sessão persistida da Loga
expirar, sem armazenar credenciais no Git e sem alterar o fluxo RouterBox.

## Decisão

A sessão persistida continua sendo o mecanismo principal. O robô só usa
`MULTIPLICA_LOGA_USER` e `MULTIPLICA_LOGA_PASSWORD` quando a navegação indica
`AUTH_EXPIRED`.

As duas variáveis serão configuradas como segredos do serviço
`apps_dmais_automacoes` no EasyPanel. Seus valores não podem aparecer em
arquivos versionados, logs ou evidências.

## Fluxo

1. Abrir a Loga com `loga-storage-state.json`.
2. Se a página autenticada estiver disponível, continuar a coleta normalmente.
3. Se houver redirecionamento para o login, preencher usuário e senha a partir
   das variáveis de ambiente e enviar o formulário.
4. Confirmar a página `Indicadores SLA e Qualidade`.
5. Renovar `loga-storage-state.json` no volume persistente, preservando
   permissão restrita.
6. Repetir a coleta uma única vez.
7. Se o login ou a repetição falhar, retornar `AUTH_EXPIRED`, sem gerar nem
   publicar pacote parcial.

## Segurança e isolamento

- Nunca registrar usuário, senha, cookies ou conteúdo do estado autenticado.
- Nunca incluir credenciais em argumentos de linha de comando ou commits.
- Manter Protheus desligado e o agendamento Multiplica desligado durante o
  dry run.
- Não alterar código, configuração, arquivos ou agendamento do RouterBox.
- Não excluir nem mover banco, volumes, pacotes ou dados existentes.

## Falhas

- Variáveis ausentes: manter `AUTH_EXPIRED` e solicitar configuração ao gestor.
- Credenciais rejeitadas: manter `AUTH_EXPIRED`, sem nova tentativa automática.
- Página autenticada não confirmada: não salvar estado e não coletar.
- Falha após renovar a sessão: encerrar sem publicação e preservar o estado
  anterior sempre que a gravação do novo estado não tiver sido concluída.

## Validação

Executar somente testes focados:

- sessão válida não usa credenciais;
- sessão expirada usa as variáveis uma vez;
- login bem-sucedido renova o estado e permite a coleta;
- variáveis ausentes ou credenciais rejeitadas retornam `AUTH_EXPIRED`;
- senha e usuário não aparecem em logs;
- arquivos RouterBox permanecem sem alteração.

A suíte completa fica reservada para um futuro merge, conforme orientação do
projeto.
