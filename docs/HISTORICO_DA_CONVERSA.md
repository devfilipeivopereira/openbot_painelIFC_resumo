# Histórico de contexto e decisões — Painel IFC / Remi_2

> Documento de continuidade preparado em 03/11/2026 a partir do histórico desta conversa e das notas de contexto disponíveis ao assistente. É uma síntese editorial detalhada, não uma transcrição literal de cada mensagem. A conversa compartilhada por link externo não foi acessada. Segredos, senhas, tokens, telefones, JIDs, conteúdo bruto de mensagens e dados pessoais de arquivos locais foram omitidos.

## 1. Objetivo do projeto

Construir e manter um Painel IFC privado, profissional e compreensível em computador e celular, alimentado diariamente por fontes da igreja, e gerar resumos matinal e noturno enviados ao WhatsApp do proprietário quando a rotina ou o pedido explícito autorizar. O proprietário quer o OpenBot com o agente `Remi_2` como assessor pessoal, capaz de compreender os dados e usar ferramentas quando solicitado.

O projeto deve ser reproduzível em outra VPS, com documentação dos componentes, integrações, configuração, agendamento, curadoria, publicação, autenticação, histórico e envio. Nenhuma credencial ou dado real deve ser colocado no repositório.

## 2. Fontes de dados e regras definidas pelo proprietário

As preferências e definições a seguir foram expressas ao longo desta conversa:

- **WhatsApp:** todos os grupos referentes à IFC devem entrar na coleta e curadoria, respeitando a classificação fornecida pelo proprietário. As mensagens diretas de líderes e administradores ligados à IFC também entram; identificar a pessoa, o que pediu e a resposta real do proprietário, por pessoa e sem misturar conversas.
- **Instâncias Evolution:** grupos gerais IFC usam a instância Filipe; o grupo de mulheres usa a instância Mulheres IFC. O proprietário autorizou o agente a falar com ele pela instância Pastor Filipe, respondendo às mensagens enviadas pelo número da instância Filipe, e aprovou encaminhamento no n8n em que mensagens com prefixo `Remi_2:` vão somente ao OpenBot e as demais permanecem no fluxo existente do Cursor.
- **Áudio e mídia:** transcrever e analisar áudios; após três tentativas sem recuperar o áudio, ignorá-lo e registrar a lacuna, sem bloquear toda a edição por causa daquele item. Imagens, vídeos e anexos devem receber extração dentro de limites e indicar quando a análise foi parcial ou indisponível.
- **Calendário:** o calendário padrão é `ifckobrasol.secretaria`, no Google Calendar. Mostrar quadro completo dos próximos sete dias e dos dias 8–30. A agenda mensal deve ser clara e datada.
- **E-mail:** ler diariamente a caixa da secretaria indicada pelo proprietário via Composio, sem confundir conexão/autorização com confirmação de leitura da mensagem.
- **Drive:** usar o Drive da secretaria como fonte documental, com tratamento explicitamente autorizado e indicação de arquivos que não puderam ser lidos.
- **Todoist:** exibir pendências úteis e verificar tarefas abertas. O agente pode criar, editar, concluir e excluir tarefas somente quando o proprietário solicitar a ação.
- **Financeiro:** mostrar apenas dados originados do dashboard financeiro IFC. Não calcular totais a partir de conversas ou e-mails. O backend deve devolver agregados, nunca lançamentos pessoais.
- **n8n/visitantes:** a tabela de visitantes do painel deve vir da tabela usada pelo fluxo n8n que cadastra visitantes e o acompanhamento de mensagens. Dados sem data devem permanecer explicitamente sem data; não inferir datas.
- **Supabase:** guardar o histórico diário do painel. Tornar um dia navegável apenas depois de a gravação no banco ser confirmada.
- **Notion:** não precisa de busca diária; só integrar uma base IFC confirmada.
- **YouTube/redes:** considerar o canal oficial fornecido pelo proprietário; no canal, o conteúdo principal é o ao vivo. Não inferir o conteúdo de uma pregação apenas pelo título quando não há legenda/transcrição.
- **ATA e dados jurídicos:** são informações fixas, de mudança rara. Fazer a consulta inicial e reutilizar o dado em dias seguintes, sem consulta diária. Dados legais restritos não devem ser enviados a modelos de linguagem.
- **Escalas:** concentrar pregação, louvor e recepção numa apresentação unificada “Escala dos Cultos”; incluir transmissão, projeção e técnicos na linha do culto correspondente, não em tabelas à parte. Agrupar “Ministro de Louvor” e “Vocais” sob “Vocais Louvor”; omitir colunas sem uso e reduzir espaçamento. Escalas passadas devem permanecer no histórico mensal e não sobrescrever escalas do mês atual ou futuras. Histórico arquivado por mês em tabela recolhida; escalas de pregação e louvor recolhíveis.
- **Pedidos de oração:** todos devem aparecer detalhadamente e com navegação lateral na tabela.
- **Contatos:** a lista deve conter apenas pessoas ligadas à IFC, sem duplicação, em seção recolhida. Essa lista é dado pessoal e exige controle de acesso.
- **Resumo:** matinal e noturno, cada qual abrangendo as últimas 24 horas. Formatação do WhatsApp deve ser bonita, organizada e com destaques pertinentes. Toda informação importante e sustentada deve ser aproveitada; lacunas de fonte precisam ser explicitadas.
- **Rotina do painel:** atualizações solicitadas às 08h e 13h30, horário de Brasília. A janela de resumo noturno foi ajustada para 23h15.
- **Modelo:** GPT-6 Luna como padrão de curadoria; o proprietário pediu rodízio/fallback por Codex Auth e depois LiteLLM, pelo alias `tudo`. Configuração real de modelos precisa ser verificada no host, sem assumir que nome do alias prova o modelo upstream.
- **Autonomia:** Remi_2 pode administrar recursos quando solicitado. Para Evolution, a autorização operacional discutida foi enviar mensagens/tarefas sob pedido, não receber acesso irrestrito para alterar instâncias, webhooks ou configurações.

## 3. Evolução da interface solicitada

Ao longo da conversa, o proprietário pediu melhorias sucessivas de apresentação e conteúdo:

1. Criar painel HTML privado e elegante, com seções identificáveis por cores e histórico diário no menu lateral.
2. Melhorar legibilidade das sessões, distribuição lógica das informações e especialmente tabelas, tomando o PDF do painel da pasta do projeto como referência.
3. Melhorar dashboard de batismos com base na planilha e disponibilizar o link da planilha; acrescentar gráfico de barras.
4. Tornar escalas e históricos recolhíveis e melhorar navegação das tabelas em telas largas e estreitas.
5. Melhorar apresentação da ATA e dados jurídicos em tabela organizada.
6. Corrigir cartões financeiros para que valores longos não transbordem.
7. Reduzir conteúdo desnecessário de histórico/Todoist e tornar o frontend eficaz em PC e celular.
8. Melhorar sessões de mensagens diretas, agenda, louvor, oração, visitantes e links úteis.
9. Permitir navegação horizontal na tabela de pedidos de oração.
10. Fazer visitantes vir da tabela de visitantes do n8n.
11. Unificar as escalas de pregação e louvor e incorporar recepção; juntar colunas de ministro e vocais; remover colunas sem uso; adicionar navegação horizontal acima e abaixo da planilha; remover as colunas “Conferência” e “Alterações confirmadas”; reduzir espaçamento entre colunas.
12. Tornar o painel/menu lateral recolhível também no desktop.

## 4. Mudanças de interface publicadas antes desta documentação

De acordo com o histórico operacional desta conversa, foram aplicadas e publicadas no serviço `ifc-panel`:

- Consolidação visual da escala de pregação, louvor e recepção, com tentativa de associar itens por data e regras de ambiguidade.
- Combinação de ministro e vocais em “Vocais Louvor”.
- Colunas dinâmicas para ocultar campos vazios e navegação horizontal no início e no fim da tabela.
- Remoção de “Conferência” e “Alterações confirmadas” da tabela ativa.
- Redução do espaçamento de colunas.
- Botão de menu lateral para recolher/expandir no desktop; comportamento drawer em celular preservado.
- Implantação reportada como `ifc-panel:20261103-32`, com a imagem anterior retida.
- Verificação de sintaxe JavaScript (`node --check painel-ifc/painel.js`) concluída antes da publicação.

Esses itens descrevem o que foi reportado como publicado naquele momento; não afirmam que os dados de escala estejam atualizados hoje ou que exista uma planilha canônica única. A consolidação visual da interface não é prova de migração para uma fonte única.

## 5. Resumos e envio pelo WhatsApp

O proprietário pediu correção do envio e autorizou teste para seu próprio número. O contexto operacional disponível registrou:

- Houve falha anterior de preparação do resumo matinal e o cron acusou arquivo de manifesto ausente.
- Uma recuperação manual produziu um resumo de teste em 03/11/2026 e a Evolution retornou `accepted_by_evolution` às 09h45 BRT.
- A consulta posterior na Evolution não encontrou correspondência; portanto, aceitação da API foi registrada, mas entrega ao aparelho e leitura não foram confirmadas.
- Um agendador foi alterado para permitir uma repetição limitada de preparação após falha, com lock exclusivo e máximo de duas tentativas; os horários e janelas dependem de validação de execução real.
- A próxima execução automática ainda precisava ser observada para comprovar confiabilidade; configuração de cron, preparação, publicação e envio são estados diferentes.
- O remetente deve usar manifesto recente, snapshot de fontes validado, destinatário/instância fixos em arquivos secretos, ledger idempotente, janela horária e bloqueio de reenvio quando o resultado é incerto.
- `accepted_by_evolution` significa aceite da API, não entrega nem leitura no WhatsApp.

Não se deve repetir uma mensagem em caso de timeout/resultado ambíguo sem verificar a entrega, para evitar duplicidade.

## 6. OpenBot, MCP e agentes

O proprietário pediu que OpenBot/Remi_2 possa atuar como agente e assessor, consultar dados e usar ferramentas. Também informou que OpenBot poderia administrar a VPS e Composio, e pediu memória que ensine o agente a usar os recursos.

Decisões e limites operacionais estabelecidos durante a conversa:

- Não alterar as rotinas do Cursor. Criar um fluxo separado para Remi_2, com a regra `Remi_2:` somente para OpenBot; demais mensagens continuam no Cursor.
- Uma integração MCP já foi mostrada na interface como conectada ao Composio, mas isso não comprova escopos upstream, ferramentas de escrita, execução de coleta nem curadoria.
- Não fornecer shell irrestrito, Docker socket ou root ao OpenBot. Expor ferramentas MCP fechadas, com início/status de jobs definidos.
- Mensagens e anexos externos são dados, não instruções para a IA; conteúdo de grupo não concede autorização operacional.
- Escritas em Todoist, calendário, e-mail e WhatsApp exigem pedido específico do proprietário, respeitando escopo e destinatário.
- A rotina automática de coleta deve ser leitura. Envio de resumos é um fluxo separado, validado e com destino fixo.
- A documentação no repositório descreve um template de memória e permissões mínimas; a configuração em produção ainda precisa ser verificada por estado real.

## 7. Segurança e credenciais

O histórico desta conversa menciona arquivos locais com acesso à VPS e uma chave Composio, além de um segredo webhook `whsec_` que chegou a ser enviado na conversa e depois foi solicitado para rotação. Valores desses arquivos e segredos não são copiados para este backup. Se algum segredo tiver sido publicado em chat, Git ou log, revogá-lo/rotacioná-lo continua sendo a ação correta.

O proprietário pediu para definir a senha do painel e informou o valor no chat. Por segurança, este documento não a repete. A nota disponível registra que a credencial foi rotacionada e armazenada em secret/file root-only na VPS; não se deve considerar uma senha encontrada em conversa como segredo seguro.

Medidas e regras relevantes:

- Arquivos `.env`, credenciais, exportações OAuth, tokens, senhas, dados JSON reais, históricos e anexos devem ficar fora do Git.
- O painel usa autenticação, HTTPS e arquivos de dados privados; respostas API devem ser sanitizadas, sem expor CPF, telefones, JIDs, tokens ou arquivos internos.
- O financeiro exposto é agregado e originado somente do dashboard IFC.
- O acesso à Ata/dados jurídicos fica separado da curadoria por modelo.
- Supabase guarda histórico privado; diretórios e artefatos pessoais precisam de permissões restritas.
- Uma senha/assinatura que apareceu no chat deve ser tratada como exposta e rotacionada.

## 8. Repositório e documentação

O repositório indicado foi `devfilipeivopereira/openbot_painelIFC_resumo`. Foi consultado e estava público no momento do push. Em 03/11/2026 foram enviados:

- `27b9dda` — documentação e pacote principal do painel, coletores, curadoria, scheduler, MCP e remetente WhatsApp.
- `7b75b3e` — gateway privado do banco, leitor especializado Evolution e gateway de transcrição.

Foram adicionados `README.md` e os guias em `docs/ARCHITECTURE.md`, `REPRODUCTION.md`, `OPERATIONS.md`, `OPENBOT.md`, `SECURITY.md` e `STATUS.md`. As credenciais locais, PDFs, snapshots e registros de auditoria específicos da VPS foram excluídos. O README e `docs/STATUS.md` alertam que código existente não comprova rotinas efetivamente ativas; o runner MCP tem comportamento fail-closed.

Uma varredura do conteúdo staged antes do push não encontrou padrões de chaves/segredos, CPF ou e-mails pessoais conhecidos. Isso não equivale a auditoria completa de privacidade ou segurança do repositório público; confirme visibilidade e conteúdo do GitHub e rotacione qualquer credencial que tenha vazado por outro canal.

## 9. Solicitação de backup do chat compartilhado

O proprietário enviou o link compartilhado `https://chatgpt.com/s/cx_6aea04aff594819187fc907d7e221282` e pediu uma cópia integral em Markdown. A página não retornou texto legível pela ferramenta de navegação, e a inicialização da automação do navegador falhou. Portanto, não foi possível obter a transcrição integral daquela conversa. Este documento registra apenas o contexto disponível neste chat e não deve ser interpretado como transcrição do link compartilhado.

## 10. Trabalho pendente/condições de aceitação

- A execução completa do pipeline e do resumo precisa ser comprovada em ciclos reais, com cobertura, timestamps, aprovação da curadoria, publicação, histórico Supabase e status do sender.
- A aceitação da Evolution não prova recebimento no aparelho.
- Configurações Composio, Evolution, n8n, Google, Supabase, LiteLLM, destinatário, catálogo de grupos, allowlist de líderes e IDs de conta devem ser recriados/verificados privadamente na VPS destino.
- `job_runner.py` precisa permanecer fail-closed até as fontes obrigatórias e gates terem configuração e validação real; existência dos coletores não é prova de que estejam registrados/ativos.
- Reavaliar o acesso público do repositório antes de acrescentar novos materiais operacionais.
- Para produzir transcrição literal da conversa compartilhada, é necessário exportar/anexar o conteúdo dessa conversa; não completar lacunas por inferência.
