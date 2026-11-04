# Documentação da VPS — Painel IFC e Resumos (estado em 04/11/2026)

Esta pasta descreve **o sistema que está rodando de verdade na VPS** (não só o código do repositório), para que qualquer pessoa — ou o Codex — consiga entender, alterar e implantar sem quebrar o envio diário.

| Documento | Conteúdo |
|---|---|
| [arquitetura.md](arquitetura.md) | Componentes, contêineres, fluxo de dados, caminhos na VPS, modelos de IA |
| [agendamento.md](agendamento.md) | Todos os crons e horários, janelas de envio, fallback, arquivos de estado, envio/dry-run manual |
| [changelog-vps.md](changelog-vps.md) | Log cronológico 03/11–04/11 de cada mudança: motivo, arquivos, backups, rollback |
| [operacao-e-rollback.md](operacao-e-rollback.md) | Runbook, healthcheck, problemas conhecidos e pendências |
| [guia-codex.md](guia-codex.md) | Como o Codex deve trabalhar: onde está o código, como implantar, regras de segurança, testes |

Cópias do código vivo (sem segredos): [`vps-snapshot/2026-11-04/`](../../vps-snapshot/2026-11-04/).
Patches acumulados: [`docs/patches/`](../patches/).

> **Fonte da verdade = VPS.** Os arquivos em `vps-snapshot/` são cópias de 04/11/2026 ~09:30 BRT. Antes de editar, compare com a VPS (`diff`), porque ajustes posteriores podem ter sido feitos direto lá.

Resumo em uma frase: a cada edição (manhã/tarde/noite) a VPS coleta as fontes, o modelo `gpt-6-luna` (via Codex no contêiner `openbot`, fallback LiteLLM `tudo`) faz a curadoria, `daily_artifacts.py` monta o texto, `whatsapp_delivery.py` envia **só para o número fixo do Filipe** pela Evolution (instância "Pastor Filipe"), e o painel (`ifc-panel`) é republicado a partir de `published/dados.json`.
