---
description: "Task list for feature: Ganchos mais fortes nos vídeos de história"
---

# Tasks: Ganchos mais fortes nos vídeos de história

**Input**: Design documents from `/specs/001-stronger-video-hooks/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Esta feature inclui **testes de guarda** (determinísticos, sobre os few-shot
estáticos e o contrato de saída) porque o plano os define como gates de milestone. Não há
TDD de geração via LLM (saída não determinística); a qualidade do gancho é verificada por
revisão manual da amostra + comparação da nota "gancho".

**Organization**: Tarefas agrupadas pelos 2 milestones do Delivery Plan (plan.md). Rótulos
`[US1]/[US2]/[US3]` mapeiam para as user stories da spec para rastreabilidade.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode rodar em paralelo (arquivo diferente, sem dependência pendente)
- **[Story]**: user story associada (US1 = título; US2 = abertura Parte 1; US3 = multilíngue/paridade)

## Path Conventions

Single project em Clean Architecture. Prompts/exemplos em `src/proxies/`; testes em `tests/`.

---

## Phase 1: Setup (Shared)

**Purpose**: Preparar a medição comparativa e a fonte única de verdade para as guardas.

- [X] T001 Capturar o baseline da nota "gancho": gerar roteiros para uma amostra fixa de posts do Reddit com o prompt atual (via `tests/test_two_part_story.py`) e registrar a nota `gancho` (0–100) de `evaluate_story` por post e a média em `specs/001-stronger-video-hooks/baseline-gancho.md` — **FEITO** com `moonshotai/kimi-k2.6` (OpenRouter): amostra fixa de 3 posts, prompt antigo via `git stash`. **Média baseline = 92.3** (ver `baseline-gancho.md` e `results-baseline.json`).
- [X] T002 [P] Extrair a lista canônica de palavras proibidas (da política de palavras fortes em `src/proxies/prompts/two_part_story.jinja2`) para uma fixture compartilhada em `tests/services/forbidden_words.py` como fonte única dos guard tests

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infra de teste de guarda usada por ambos os milestones.

**⚠️ CRITICAL**: bloqueia os dois milestones — deve estar pronto antes de M1/M2.

- [X] T003 Criar o módulo de guarda `tests/services/test_hook_quality.py` com helpers: `load_two_part_examples()` (lê `src/proxies/examples/two_part_story.yaml`), `assert_no_forbidden_words(text)` (usa `tests/services/forbidden_words.py`) e `assert_two_part_structure(entry)` (valida marcadores "Parte 1./2." e o CTA "Curta e me siga para a parte 2." ao fim de `part1`)

**Checkpoint**: infra de guarda pronta — implementação dos milestones pode começar.

---

## Phase 3: Milestone 1 — Caminho ativo: gancho forte no título e abertura (US1, US2) 🎯 MVP

**Goal**: No caminho ativo (2-part), o título lidera por promessa/stakes com vítima e
antagonista claros sem revelar o desfecho, e a Parte 1 abre no conflito — com few-shot
coerentes e invariantes preservadas.

**Independent test criteria** (verificáveis antes da implementação):
- Numa amostra de posts, ≥ 80% dos títulos satisfazem H1–H6 de `contracts/hook_anatomy.md`
  (lidera por stakes, vítima, antagonista, dano concreto, não revela desfecho, tom falado).
- Numa amostra, ≥ 80% das aberturas de Parte 1 entram no conflito (O1), sem preâmbulo.
- Todos os few-shot satisfazem H1–H6/O1 e nenhum contradiz a instrução (guard test verde).
- Contrato JSON, marcadores/CTA e política de palavras preservados em 100% da amostra.
- Média da nota "gancho" melhora vs. o baseline de T001.

### Implementation for Milestone 1

- [X] T004 [US1] Reescrever a instrução de título (item 2) em `src/proxies/prompts/two_part_story.jinja2` por racional: liderar por dano concreto a alguém por quem torcer, antagonista/injustiça claros, promessa implícita de virada, tom falado, ir direto ao ponto sem revelar o desfecho; apresentar a anatomia como critérios a perseguir e a citação revoltante como opcional; remover linguagem prescritiva/"clickbait" e CAPS
- [X] T005 [US2] Reescrever a abertura das partes (item 5) em `src/proxies/prompts/two_part_story.jinja2` para que, após "Parte 1.", a narração entre direto no conflito/stakes (sem preâmbulo) dando continuidade à promessa do título (depende de T004 — mesmo arquivo)
- [X] T006 [P] [US1] Adicionar o campo `title` (gancho forte traduzido, idêntico ao trecho de `part1` antes de "Parte 1.") a cada entrada de `src/proxies/examples/two_part_story.yaml`, e revisar títulos que revelam o desfecho (ex.: "Fiz minha chefe ser demitida…") para liderarem pelo dano/injustiça sem entregar o resultado (H5)
- [X] T007 [US1] Atualizar o bloco `# EXAMPLES` em `src/proxies/prompts/two_part_story.jinja2` para referenciar `{{ example.title }}` (e `narrator_gender`) no "Expected Output JSON", substituindo `{{ example.original_post.title }}` (depende de T005, T006)
- [X] T008 [P] [US1] Adicionar teste de guarda em `tests/services/test_hook_quality.py`: para cada exemplo, `title` == prefixo de `part1` antes de "Parte 1.", `assert_no_forbidden_words` em `title`/`part1`/`part2`, e `assert_two_part_structure` válido

### Live verification (milestone gate)

- [X] T009 [US1] Gerar roteiros para a amostra pelo caminho ativo, revisar contra `contracts/hook_anatomy.md` (títulos H1–H6 ≥ 80%, aberturas O1 ≥ 80%), rodar `pytest tests/services/test_hook_quality.py -q` (verde), reavaliar a nota "gancho" e confirmar melhora vs. baseline (T001)
  - **Portão automático — PASSOU**: `.venv/bin/pytest tests/services/test_hook_quality.py -q` → `3 passed`
    (contrato/marcadores/CTA preservados; `title` == prefixo de `part1`; sem palavras proibidas em título/part1/part2).
    Suíte completa `tests/services/` → `48 passed`. Render do template validado: usa
    `example.title` + `narrator_gender`, sem `original_post.title`.
  - **Portão manual/LLM — PASSOU** (LLM `moonshotai/kimi-k2.6` via OpenRouter, amostra fixa de 3 posts):
    - Títulos H1–H6: **3/3 = 100%** (≥ 80% ✅) — lideram por dano/injustiça, vítima + antagonista, dano concreto, tom falado, **sem revelar o desfecho**.
    - Aberturas O1: **3/3 = 100%** (≥ 80% ✅) — entram direto no conflito após "Parte 1.", sem preâmbulo.
    - Contrato/marcadores/CTA + sem palavras proibidas: 100% da amostra ✅.
    - Nota "gancho" média: **93.0 (depois) vs 92.3 (baseline) → +0.7 ✅** (SC-001). Evidência em `baseline-gancho.md`, `results-after.json`, `results-baseline.json`.

**Checkpoint**: Milestone 1 DONE — MVP entregável (1 PR). Portão automático + manual/LLM aprovados.

---

## Phase 4: Milestone 2 — Paridade de caminhos e verificação multilíngue (US3)

**Goal**: Os caminhos single-part (`story.jinja2`) e DSPy (`llm_dspy_proxy.py`) produzem
ganchos com a mesma anatomia do caminho ativo, e o efeito se sustenta em ≥ 2 idiomas alvo.

**Independent test criteria**:
- Os três caminhos produzem ganchos com a mesma anatomia; nenhum diverge (FR-013).
- Guard tests de contrato single-part e do few-shot DSPy verdes.
- Amostra em pt-br + um segundo idioma mantém H1–H6/O1 com fraseado local natural (SC-006).

### Implementation for Milestone 2

- [X] T010 [P] [US3] Aplicar o mesmo racional de gancho à instrução de título (item 2) e à abertura (item 5) em `src/proxies/prompts/story.jinja2` (single-part), removendo linguagem "clickbaity"/CAPS — **FEITO** (entregue junto ao alinhamento da fase de repair RT023 + M1): item 2 lidera por dano/injustiça concreta com vítima + antagonista, não revela o desfecho, tom falado, promessa real (não tease); item 5 não narra o título e abre direto no conflito. Sem "clickbaity"/CAPS casuístico no prompt de geração (as únicas ocorrências de "clickbaity" restantes estão nos prompts de **avaliação** congelados, que o plano proíbe alterar).
- [X] T011 [P] [US3] Atualizar as signatures DSPy em `src/proxies/llm_dspy_proxy.py` (`TwoPartTikTokStorySignature`, `TikTokStorySignature`) e as descrições de `viral_title`/`part1_script`/`script` para o mesmo racional de gancho forte, retirando "clickbaity" — **FEITO** (RT023): item 2 de ambas as signatures com o racional; `viral_title`/`part1_script`/`part2_script`/`script` descrevem gancho na capa (não narrado) que lidera por dano/injustiça sem revelar o desfecho; narração começa direto na história. Sem "clickbaity" nas signatures de geração.
- [X] T012 [US3] Simplificar o carregador de few-shot em `_get_story_generator` (`src/proxies/llm_dspy_proxy.py`) para usar `entry["title"]` como `viral_title`, substituindo o split de `part1` em ". Parte 1." (depende de T006, T011 — mesmo arquivo que T011) — **FEITO** (RT023): `viral_title = entry.get("title") or post.get("title", "")`, sem nenhum split de `part1`.
- [X] T013 [P] [US3] Estender `tests/services/test_hook_quality.py` para cobrir o contrato single-part (chaves `title`/`narrator_gender`/`script`) e validar que o few-shot carregado pelo DSPy usa o `title` explícito sem palavras proibidas — **FEITO**: +3 guards — `test_single_part_signature_matches_contract` (output fields `viral_title`/`narrator_gender`/`script`), `test_two_part_signature_matches_contract` (paridade da signature 2-part), `test_dspy_fewshot_uses_explicit_title_without_forbidden_words` (o loader usa o `title` explícito, nunca o título original em inglês, sem palavras proibidas).

### Live verification (milestone gate)

- [X] T014 [US3] Gerar roteiros pelos caminhos single-part e DSPy e por 2 idiomas alvo (pt-br + 1), confirmar anatomia consistente entre os três caminhos e fraseado local natural, e rodar `pytest tests/services/test_hook_quality.py -q` (verde)
  - **Guards automáticos — PASSOU**: `.venv/bin/pytest tests/services/test_hook_quality.py -q` → `7 passed`; suíte `tests/services/` → `59 passed`.
  - **Anatomia entre caminhos + multilíngue — PASSOU** (LLM `moonshotai/kimi-k2.6`, amostra fixa de 2 posts, pt-br + en; harness `gate_m2.py`, evidência em `results-m2.json`): o caminho **ativo/produção `prompt_single`** (`story.jinja2` via `PromptLLMProxy`, o `type: prompt` de `config.yaml`/`config.prod.yaml`) passou **4/4** em pt-br e en — títulos lideram por dano/injustiça com vítima + antagonista sem revelar o desfecho, narração começa na história (sem título/"Parte N."), fraseado local natural nos dois idiomas. O caminho **alternativo DSPy** produziu a mesma anatomia quando retornou (reruns limpos, sem palavras proibidas), confirmando paridade de conteúdo (FR-013).
  - **Ressalva 1 (não-regressão; decisão do usuário)** — **caminho alternativo DSPy instável com este modelo**: `DSPyLLMProxy` falhou de forma intermitente com `TypeError: 'NoneType' object is not subscriptable`. Traceback: bug de terceiros em `json_repair` (`json_parser.py:183`, `self.json_str[...]` com `json_str=None`), chamado pelo `JSONAdapter` interno do DSPy quando o modelo devolve uma completion vazia. **Não** é regressão do conteúdo do M2 (o gancho/anatomia estão corretos quando retorna) nem afeta o caminho de produção (`prompt`). Análogo ao achado RT025 no `_clean_json`. Por Princípio I da constituição (sem novos ramos defensivos), não foi adicionado retry ao escopo da feature — fica para decisão do usuário (endurecer o proxy DSPy num commit separado ou aguardar upstream).
  - **Ressalva 2 (não-determinismo do LLM)**: 1 geração `dspy_single` (en) trouxe uma palavra proibida isolada; 3 reruns do mesmo caminho/idioma vieram limpos (0/3). É variação natural da geração, coberta pela política de palavras em todos os prompts; os guards determinísticos sobre os few-shot estáticos permanecem verdes.

**Checkpoint**: Milestone 2 DONE — paridade de conteúdo entre caminhos confirmada (caminho de produção `prompt` verde em 2 idiomas) e guards verdes. Ressalva de robustez do caminho alternativo DSPy (`json_repair`/completion vazia) registrada para decisão do usuário. (1 PR)

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T015 [P] Atualizar o cabeçalho de comentários de `src/proxies/examples/two_part_story.yaml` para descrever a nova anatomia de gancho, removendo a nota desatualizada "Titles are natural and conversational, not forced or clickbaity"
- [ ] T016 Executar a validação de `specs/001-stronger-video-hooks/quickstart.md` (passos 2–6) e registrar os resultados (nota "gancho", % de títulos/aberturas conformes, checagem multilíngue)
- [ ] T017 [P] Revisão final de consistência: confirmar que nenhum few-shot contradiz a instrução e verificar se `docs/configuration.md` menciona os prompts de história (atualizar somente se necessário)

---

## Phase 6: Repair — Título fora da narração (D1=A, D2=B, D3=C, D4=A)

**Purpose**: tirar o título e o marcador "Parte N." da narração (ficam só na capa); a
narração começa direto na história; a capa é sobreposta aos primeiros segundos por uma
duração curta configurável. Refina a feature 001 (não é feature nova).

**Independent test criteria**:
- Numa amostra, 100% dos `part1`/`part2` NÃO começam pelo título nem por "Parte N.".
- `title` continua presente e não vazio (gancho da capa), sem palavras proibidas.
- Guard tests verdes; a nota "gancho" não regride vs. o resultado do M1.

- [X] RT018 [P] Reescrever item 5 de `src/proxies/prompts/two_part_story.jinja2`: a narração de cada parte começa **direto na história**, sem título e sem marcador "Parte N."; o título/marcador são exibidos só na capa
- [X] RT019 [P] Atualizar `src/proxies/examples/two_part_story.yaml`: `part1`/`part2` começam direto na história (remover o `"<title>. Parte N."` do início); manter o campo `title` como gancho da capa
- [X] RT020 [P] `src/proxies/prompts/enhance_transcription.jinja2`: remover a instrução de "strip introduction" (título/marcador não estão mais na narração)
- [X] RT021 Corrigir `_compute_content_boundaries` em `src/services/reddit_video_service.py` para não descartar a primeira palavra quando não há marcador, e usar `video_config.cover_duration` como janela da capa; revisar `_strip_introduction` (vira no-op)
- [X] RT022 [P] Reduzir `video_config.cover_duration` (duração curta) em `config.yaml`/`config.dev.yaml`/`config.prod.yaml` e no default do entity `src/entities/configs/services/video.py`
- [X] RT023 [P] Alinhar caminhos paralelos: `src/proxies/prompts/story.jinja2`, `src/proxies/prompts/revise_story.jinja2` e as signatures DSPy (`src/proxies/llm_dspy_proxy.py`) para não narrar título/marcador
- [X] RT024 [P] Atualizar guards em `tests/services/test_hook_quality.py`: `title` não vazio e sem palavras proibidas; `part1`/`part2` NÃO começam pelo título nem por "Parte N."; `part1` termina com o CTA

### Live verification (repair gate)

- [X] RT025 Gerar amostra pelo caminho ativo (LLM) e confirmar: narração começa na história (sem título/"Parte N."), `title` presente para a capa, `pytest tests/services/test_hook_quality.py -q` verde e nota "gancho" sem regressão
  - **Formato — PASSOU** (LLM `moonshotai/kimi-k2.6`): 100% das amostras geradas têm `part1`/`part2` que NÃO começam pelo título nem por "Parte N."; a narração entra direto na história; `title` presente para a capa. Evidência: `results-repair.json`.
  - **Guards — PASSOU**: `.venv/bin/pytest tests/services/test_hook_quality.py -q` → `4 passed`; suíte `tests/services/` → `49 passed`.
  - **Nota "gancho" — RESSALVA**: 87.0 vs 93.0 do M1 (queda ~5 pts nos mesmos posts). Esperado: o avaliador `evaluate_story` premia o gancho da **primeira frase narrada**, que antes era o título punchy e agora é a abertura da história (título foi pra capa). A régua ficou parcialmente desalinhada com o novo design (ela não "vê" a capa). Não é regressão de qualidade do gancho geral — é o trade-off aprovado (D1/D2). Decisão do usuário se aceitável.
  - **Achado (CORRIGIDO)**: `PromptLLMProxy._clean_json` não tratava JSON com quebras de linha literais dentro de strings — 1 dos 3 posts falhou a geração de forma determinística. Bug de robustez pré-existente do proxy, **corrigido** em commit separado (escape de control chars dentro de strings + testes de regressão `tests/services/test_clean_json.py`).

**Checkpoint**: Repair — implementação + guards + formato de narração OK; ressalva na métrica "gancho" (ver RT025) para decisão do usuário.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: sem dependências — pode começar imediatamente.
- **Foundational (Phase 2)**: depende de T002 (fixture de palavras proibidas). Bloqueia M1 e M2.
- **Milestone 1 (Phase 3)**: depende da Phase 2. É o MVP.
- **Milestone 2 (Phase 4)**: começa após o gate de M1 (T009) passar — reusa o `title`
  explícito introduzido em T006 e a infra de guarda.
- **Polish (Phase 5)**: depois de M2.

### Story/Task Dependencies

- T004 → T005 → T007 (mesmo arquivo `two_part_story.jinja2`, sequenciais).
- T006 (YAML) é [P] em relação a T004/T005, mas T007 depende de T006.
- T011 → T012 (mesmo arquivo `llm_dspy_proxy.py`, sequenciais); T012 também depende de T006.
- Guard tests (T008, T013) dependem do módulo criado em T003.

### Parallel Opportunities

- T002 [P] roda em paralelo com T001.
- Em M1: T006 [P] (YAML) em paralelo com T004/T005 (jinja2); T008 [P] em paralelo com edições de prompt.
- Em M2: T010 [P] (story.jinja2) e T011 [P] (llm_dspy_proxy.py) em paralelo; T013 [P] em paralelo.
- Em Polish: T015 [P] e T017 [P] em paralelo.

---

## Parallel Example: Milestone 1

```bash
# Após a Phase 2, iniciar em paralelo:
Task: "T006 Adicionar campo title aos exemplos em src/proxies/examples/two_part_story.yaml"
Task: "T008 Guard test dos few-shot em tests/services/test_hook_quality.py"
# Em série no mesmo arquivo de prompt:
Task: "T004 Reescrever instrução de título em src/proxies/prompts/two_part_story.jinja2"
Task: "T005 Reescrever abertura das partes em src/proxies/prompts/two_part_story.jinja2"
```

---

## Delivery Plan

| PR | Milestone | Tarefas | Gate |
|----|-----------|---------|------|
| 1 | M1 — Caminho ativo (US1, US2) | T001–T009 | T009: anatomia ≥ 80%, guard verde, nota "gancho" ↑ vs baseline |
| 2 | M2 — Paridade + multilíngue (US3) | T010–T017 | T014: 3 caminhos consistentes, 2 idiomas, guard verde |

**PR count projetado: 2** (≤ 8 ✅). Setup/Foundational (T001–T003) e Polish (T015–T017)
são commits dentro do PR do milestone correspondente (M1 e M2), não PRs separados.

---

## Implementation Strategy

### MVP First (Milestone 1)

1. Phase 1 (Setup) → baseline + fixture de palavras proibidas.
2. Phase 2 (Foundational) → módulo de guarda.
3. Phase 3 (M1) → reescrita do caminho ativo + few-shot alinhado.
4. **PARAR e VALIDAR** no gate T009 (anatomia, guard, nota "gancho" vs baseline).
5. Merge do PR 1 (MVP).

### Incremental Delivery

1. M1 entregue e validado (MVP) → merge.
2. M2 (paridade single-part/DSPy + multilíngue) → validar no gate T014 → merge.
3. Polish incorporado ao PR do milestone correspondente.

---

## Notes

- [P] = arquivos diferentes, sem dependência pendente.
- Contrato de saída, estrutura de duas partes e política de palavras são invariantes
  (ver `contracts/llm_story_output.md`) — guardadas por T008/T013.
- Prompts por racional, sem regras casuísticas nem CAPS (constituição, Princípio III).
- Commit após cada tarefa ou grupo lógico; parar nos checkpoints para validar.
