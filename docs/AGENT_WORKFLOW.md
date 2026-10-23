# Workflow de agentes do RARNESS

Este documento descreve como o trabalho de desenvolvimento do RARNESS é dividido entre agentes.

## Papéis

- **Orquestrador**: decompõe o trabalho em pacotes, define interfaces, integra os resultados, roda os testes e faz o commit.
- **Agentes de módulo**: cada um implementa um módulo do pacote `rarness/` e o teste correspondente. Cada agente só edita os arquivos do seu pacote.
- **Revisor**: lê o diff integrado procurando falhas de segurança e de lógica, e não edita código.

## Regras de propriedade de arquivos

- Um agente de módulo edita apenas `rarness/<modulo>.py` e `tests/test_<modulo>.py`.
- `opencode.py`, `api/index.py`, `index.html` e `rarness_security.py` são integrados pelo orquestrador, nunca por agentes de módulo.
- Nenhum agente faz commit, push ou PR. Isso fica com o orquestrador.

## Interfaces compartilhadas

| Módulo | Responsabilidade | Dependências |
|---|---|---|
| `rarness/planner.py` | Modelo de plano, validação, ordem de tarefas | stdlib |
| `rarness/project_fs.py` | Escrita de arquivos do projeto, backup, ZIP | `rarness_security` |
| `rarness/executor.py` | Execução de comandos com limites | stdlib |
| `rarness/repair.py` | Ciclo de testes, correção com limite de tentativas, regressões | `executor` |
| `rarness/state.py` | Estado persistente em `.rarness/state.json` | stdlib |

## Portões de qualidade

1. Cada módulo passa nos seus próprios testes antes de ser aceito.
2. A suíte completa (`python -m unittest discover -s tests -v`) passa após a integração.
3. O revisor não encontra problemas de segurança abertos (segredos, caminhos fora da raiz, comandos sem limite).
4. Só então o orquestrador faz o commit na branch designada.

## Escopo desta rodada

Módulos do núcleo, sem integração com o modelo de IA nem com a interface web. A integração com a CLI (`/plano`, `/construir`) e com o navegador é a próxima rodada.
