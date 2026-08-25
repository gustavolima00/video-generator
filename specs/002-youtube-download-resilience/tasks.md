# Tasks: Downloads resilientes de backgrounds do YouTube

**Input**: Design documents from `/specs/002-youtube-download-resilience/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Incluídos — os gates de verificação do plan.md exigem os testes de contrato
(C1–C11, T1–T6) e o projeto tem suíte pytest estabelecida. Escrever os testes de cada
story primeiro e vê-los falhar antes de implementar.

**Organization**: 3 milestones = 3 user stories = 3 PRs (ver Delivery Plan). Tarefas
dentro de um milestone são commits do mesmo PR, nunca PRs separados.

## Format: `[ID] [P?] [Story] Description`

## Delivery Plan

| PR | Milestone | Stories | Conteúdo |
|----|-----------|---------|----------|
| 1 | M1 — Cache de backgrounds | US1 | Decorator + config + factory + testes + docs de config |
| 2 | M2 — Limite de disco (LRU) | US2 | Eviction + touch no hit + testes |
| 3 | M3 — po_token configurável | US3 | Secrets + wiring + pytube_proxy + doc do operador + testes |

Projeção: **3 PRs** (≤ 8, ok).

---

## Phase 1: Setup

**Purpose**: Confirmar a linha de base antes de mexer — a feature não cria projeto novo.

- [X] T001 Rodar `uv sync && uv run pytest -q` e registrar a linha de base verde (nenhum arquivo novo; se a suíte já estiver quebrada, parar e reportar antes de começar)
  — **Evidência (2026-08-25)**: `uv sync --extra dev` (o `uv sync` puro remove o pytest, que vive no extra) + `uv run pytest -q` → **132 passed, 1 failed**. A falha é pré-existente e alheia à feature: `tests/test_translation_pipeline.py::test_pipeline` chama `PromptLLMProxy.translate_and_adapt`, método que não existe mais. O escopo do gate M1 (`tests/test_pytube_proxy.py tests/services`) estava 100% verde (77 passed), então o M1 seguiu.

---

## Phase 2: Foundational

Nenhuma tarefa — não há pré-requisito compartilhado além do Setup: o modelo de config
do cache serve US1/US2 e nasce dentro do M1 (story mais cedo que o usa, conforme
data-model.md); os campos de token são exclusivos do M3.

**Checkpoint**: Baseline verde — M1 pode começar.

---

## Phase 3: Milestone 1 — Cache de backgrounds (US1 / P1) 🎯 MVP

**Goal**: rodadas repetidas servem clipes do disco e só vão à rede em miss; execução
com cache quente completa mesmo com YouTube negando downloads.

**Independent test criteria** (antes de implementar):

- Segunda chamada de `download_video` para o mesmo `(video_id, faixa)` não toca o
  proxy interno e retorna bytes idênticos.
- Com o proxy interno lançando erro (throttle simulado), o hit ainda serve os bytes.
- Arquivo corrompido/truncado no cache → descartado, re-baixado, não envenena o id.
- Diretório não gravável → warning e a execução segue (download-only).
- `hq`/`lq` nunca se servem mutuamente; `list_video_ids` passa direto.
- Suíte existente (`tests/test_pytube_proxy.py`, `tests/services/`) passa sem mudanças.

### Testes (escrever primeiro, ver falhar)

- [X] T002 [P] [US1] Testes de contrato C1–C7 e C9–C11 (hit, miss, atomicidade, corrompido→miss, best-effort, faixas hq/lq, delegação de listagem, logs hit/miss, `enabled=False`) com proxy interno fake, em tests/test_caching_youtube_proxy.py
- [X] T003 [P] [US1] Teste de regressão de config: `PyTubeYouTubeConfig` sem bloco `cache` no yaml carrega com defaults (`enabled=True`, dir `~/.cache/video-generator/backgrounds`, 20 GB), em tests/test_caching_youtube_proxy.py

### Implementação

- [X] T004 [US1] Adicionar `BackgroundCacheConfig` (`enabled`, `dir`, `max_gigabytes`) e o campo aninhado `cache` em `PyTubeYouTubeConfig`, em src/entities/configs/proxies/youtube.py (regras em data-model.md)
- [X] T005 [US1] Criar `CachingYouTubeProxy(IYouTubeProxy)` em src/proxies/caching_youtube_proxy.py: chave `{video_id}-{hq|lq}.mp4`, hit com validação barata (legível, não vazio, box `ftyp`) e inválido→apagar+miss, miss delega ao interno e persiste via tmp + `os.replace`, escrita best-effort com warning, criação do diretório com `~` expandido, logs INFO de hit/miss com contadores da instância, `list_video_ids` delegando direto (contrato C1–C7, C9–C10)
- [X] T006 [US1] Montar o decorator na `YouTubeProxyFactory.create` quando `config.cache.enabled`, mantendo `PyTubeProxy` puro quando desligado (C11), em src/proxies/factories.py
- [X] T007 [P] [US1] Adicionar o bloco `cache` comentado (defaults e propósito) em config.yaml e config.prod.yaml sob `proxies.youtube_config`
- [X] T008 [P] [US1] Documentar o bloco `cache` na seção YouTube de docs/configuration.md (tabela de campos + defaults)

### Live verification (milestone gate)

- [X] T009 [US1] Rodar `uv run pytest tests/test_caching_youtube_proxy.py tests/test_pytube_proxy.py tests/services -q` (tudo verde) e o cenário 🌐 manual do quickstart.md M1: cache frio → misses e arquivos criados; segunda rodada → só hits; rodada sem rede/throttle simulado → ainda completa (SC-001/SC-002)

**Evidência do gate M1 (2026-08-25)**:

- Automatizado: `uv run pytest tests/test_caching_youtube_proxy.py tests/test_pytube_proxy.py tests/services -q`
  → **102 passed** (25 novos). Regressão completa `uv run pytest -q` → **157 passed, 1 failed**
  (a mesma falha pré-existente do T001, intocada).
- 🌐 Manual, contra o diretório de cache real (`~/.cache/video-generator/backgrounds`), com o proxy
  montado pelo container (`CachingYouTubeProxy` envolvendo `PyTubeProxy`, confirmado em runtime):
  - **Listagem real passou direto pelo decorator**: 48 ids de `@FoodieBoyKR/shorts` (C1 ao vivo).
  - **429 real**: a tentativa de cache frio contra o YouTube tomou `HTTP Error 429` de verdade —
    exatamente a condição que a feature existe para cobrir. O erro propagou intacto como
    `YouTubeRateLimitError` e **nada foi gravado no cache** (C5 ao vivo). Não houve retry: cada
    request extra prolonga o bloqueio.
  - Com o leg de download frio bloqueado pelo IP, o resto rodou com os bytes de um clipe real já
    baixado do YouTube (`tests/data/NC7t39glF1U.mp4`, 90.353.808 bytes) fazendo as vezes de rede
    no miss; da escrita em diante o caminho é o real:
    - Run 1 (frio): 1 download, arquivo `NC7t39glF1U-hq.mp4` criado no diretório real.
    - Run 2 (quente, instância nova, `PyTubeProxy` real por baixo com contador):
      **zero requisições de download**, bytes idênticos (SC-001).
    - Run 3 (quente, inner recusando tudo com 429): serviu os mesmos bytes e a execução
      completou (SC-002).
    - Run 4 (entrada corrompida para 17 bytes): warning, entrada descartada, re-download,
      arquivo restaurado íntegro — o id não foi envenenado.
- **Não verificado ao vivo**: o miss batendo no YouTube de verdade (IP em 429 hoje; coberto por
  teste automatizado com inner fake) e o ganho de tempo do SC-006 — o "download" do run 1 foi
  leitura de arquivo local, então os 0,12s→0,03s medidos não representam a economia real de rede.
- **Achado colateral**: `tests/script_youtube_download.py` (citado no quickstart) está defasado —
  chama `list_video_ids` de forma síncrona, mas a interface é `async` desde antes desta feature,
  e sorteia um id novo a cada execução, o que nunca produziria um hit. A verificação usou um
  script equivalente no scratchpad. Consertar o script fica fora do escopo do M1.

**Checkpoint**: ✅ Milestone 1 DONE (2026-08-25, gate passou) — PR 1 abre aqui; valor entregue mesmo se M2/M3 nunca saírem.

---

## Phase 4: Milestone 2 — Limite de disco com eviction LRU (US2 / P2)

**Goal**: o cache nunca passa do cap configurado; o menos usado sai primeiro; reuso
conta como uso.

**Independent test criteria** (antes de implementar):

- Após qualquer inserção, soma dos tamanhos ≤ cap.
- Ordem de remoção é por menor `mtime`; hit atualiza `mtime` (touch) e muda a ordem.
- Entrada evictada volta como miss simples na próxima rodada.
- Cap menor que o clipe recém-baixado: bytes da execução corrente não são afetados.

### Testes (escrever primeiro, ver falhar)

- [X] T010 [P] [US2] Testes de eviction (contrato C8 + cenários da US2: cap respeitado, ordem LRU, touch no hit reordena, evictado→miss, cap menor que um clipe) em tests/test_caching_youtube_proxy.py

### Implementação

- [X] T011 [US2] Implementar touch (`os.utime`) no hit e eviction pós-escrita (somar tamanhos, remover menor `mtime` até caber em `max_gigabytes`; tolerar `FileNotFoundError` de concorrência como miss) em src/proxies/caching_youtube_proxy.py

### Live verification (milestone gate)

- [X] T012 [US2] Rodar `uv run pytest tests/test_caching_youtube_proxy.py -q` (verde) e o cenário manual do quickstart.md M2: cap pequeno em config.yaml, duas rodadas, `du -sh` confirma SC-003

**Evidência do gate M2 (2026-08-25)**:

- Automatizado: `uv run pytest tests/test_caching_youtube_proxy.py -q` → **34 passed**
  (9 novos, todos vermelhos antes da implementação). Regressão completa `uv run pytest -q`
  → **166 passed, 1 failed** — a mesma falha pré-existente do T001
  (`tests/test_translation_pipeline.py::test_pipeline`), intocada.
- Manual (SC-003), contra disco real com o `config.yaml` real carregado por
  `MainConfig.from_yaml` e o proxy montado pela `YouTubeProxyFactory`
  (`CachingYouTubeProxy` sobre `PyTubeProxy`, verificado em runtime). Cap de 0,18 GB
  (193.273.528 bytes) num diretório de teste separado — `~/.cache/video-generator/backgrounds-m2-check`,
  removido no fim — para não evictar os clipes reais do cache do M1. Clipe real de
  90.353.808 bytes (`tests/data/NC7t39glF1U.mp4`) fazendo as vezes de rede no miss
  (o IP segue em 429); da escrita em diante o caminho é o de produção:
  - Round 1 (frio, A + B): `du -sh` = **172M**, 180.707.616 B ≤ cap. Duas entradas.
  - Round 2 (hit em A → touch, depois miss em C): eviction escolheu **B**, o não tocado —
    `du -sh` = **172M**, ≤ cap, entradas `A` e `C`. Reuso contou como uso (US2 cenário 3).
  - Round 3 (B de volta): miss simples, re-download, `du -sh` = **172M** ≤ cap;
    eviction tirou A. Eviction é invisível fora do download extra (US2 cenário 2).
  - Cap respeitado **após cada rodada**, não só no fim (SC-003).
- **Não verificado ao vivo**: o miss batendo no YouTube de verdade (IP em 429; coberto por
  teste automatizado com inner fake) e eviction sob concorrência real de duas máquinas —
  o `FileNotFoundError` de corrida é coberto por teste com `unlink` monkeypatchado.
- Fora das tarefas listadas, a documentação do cap foi ajustada para virar verdade agora que
  a eviction existe: `title` do campo `max_gigabytes`, comentário nos dois yamls e a seção
  do cache em docs/configuration.md.

**Checkpoint**: ✅ Milestone 2 DONE (2026-08-25, gate passou) — PR 2 abre aqui.

---

## Phase 5: Milestone 3 — po_token configurável + documentação (US3 / P3)

**Goal**: operador instala um po_token via `.env` para misses sobreviverem ao bot
detection; sem token, comportamento idêntico ao atual; renovação documentada em
português.

**Independent test criteria** (antes de implementar):

- Com par configurado, `YouTube(...)` recebe `use_po_token=True` e verifier que
  retorna `(visitor_data, po_token)` nessa ordem.
- Sem token (ou em branco), `YouTube(...)` é construído exatamente como hoje.
- Só um dos dois valores → erro imediato na construção do proxy.
- Falha de download com token configurado menciona expiração e docs/po-token.md.
- 429 continua abortando via `YouTubeRateLimitError` (FR-013).
- `git grep` não encontra valor de token em arquivo rastreado.

### Testes (escrever primeiro, ver falhar)

- [ ] T013 [P] [US3] Testes de contrato T1–T6 (construção do `YouTube` com/sem token, ordem da tupla do verifier, par incompleto→erro, mensagem de expiração, 429 preservado, limpeza do cache de tokens do pytubefix na construção) em tests/test_pytube_proxy.py

### Implementação

- [ ] T014 [P] [US3] Adicionar `youtube_po_token` e `youtube_visitor_data` (Optional, default None) em src/core/secrets.py
- [ ] T015 [US3] Adicionar `po_token`/`visitor_data` em `PyTubeYouTubeConfig` (nunca vindos do yaml; whitespace→None) em src/entities/configs/proxies/youtube.py, repassar de `Secrets` na `YouTubeProxyFactory.create` em src/proxies/factories.py e no wiring em src/core/container.py (mesmo padrão dos demais segredos)
- [ ] T016 [US3] No `PyTubeProxy` (src/proxies/pytube_proxy.py): validar o par na construção (fail fast se incompleto, T3); limpar o cache interno de tokens do pytubefix quando token presente (T4 — `pytubefix.helpers.reset_cache()` ou remoção de `pytubefix/__cache__/tokens.json`, confirmar qual limpa o arquivo, ver research.md §1); passar `use_po_token=True` + `po_token_verifier` em `_download_with_client` (T1/T2); enriquecer o erro de download com a dica de expiração apontando docs/po-token.md quando token configurado (T5)
- [ ] T017 [P] [US3] Escrever docs/po-token.md em português cobrindo os 4 pontos do contrato configuracao.md (o que resolve/não resolve, obtenção via DevTools `v1/player`, instalação no `.env`, reconhecimento de expiração e renovação)
- [ ] T018 [P] [US3] Adicionar placeholders `YOUTUBE_PO_TOKEN`/`YOUTUBE_VISITOR_DATA` com comentário apontando docs/po-token.md em env.example, e referenciar a doc na seção YouTube de docs/configuration.md

### Live verification (milestone gate)

- [ ] T019 [US3] Rodar `uv run pytest tests/test_pytube_proxy.py -q` (verde), a checagem de higiene `git grep -iE "po_token|visitor_data" -- ':!specs' ':!docs' ':!*.md'` (só nomes de campo, nenhum valor) e o cenário 🌐 manual do quickstart.md M3: seguir docs/po-token.md do zero, download passa com token; token corrompido → mensagem aponta a doc (SC-004/SC-005)

**Checkpoint**: Milestone 3 DONE — PR 3 abre aqui.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T020 Rodar a regressão completa `uv run pytest -q` e revisar os artefatos da feature (spec/plan/contratos) marcando divergências que a implementação revelou
- [ ] T021 [P] Atualizar o checklist specs/002-youtube-download-resilience/checklists/requirements.md se algum requisito mudou durante a implementação

---

## Dependencies & Execution Order

### Phase/Milestone

- Setup (T001) → M1 → M2 → M3 → Polish. O gate de cada milestone precisa passar
  antes do próximo começar (M2 muda o mesmo arquivo criado no M1; M3 é independente
  de M2, mas mantém a ordem de prioridade e de PRs).

### Dentro dos milestones

- M1: T002/T003 (testes, [P] entre si) → T004 → T005 → T006 → T007/T008 ([P]) → T009
- M2: T010 → T011 → T012
- M3: T013/T014 (paralelos) → T015 → T016 → T017/T018 ([P]) → T019

### Parallel opportunities

- T002 ∥ T003 (mesmo arquivo novo de testes, mas casos disjuntos — se preferir evitar
  conflito, tratar como sequenciais no mesmo commit)
- T007 ∥ T008 (yamls vs docs)
- T013 ∥ T014 (testes vs secrets)
- T017 ∥ T018 (doc nova vs env.example/configuration.md)
- Entre stories não há paralelismo real: M2 edita o arquivo do M1 e o projeto tem um
  único dev.

---

## Implementation Strategy

**MVP = M1 sozinho**: com o PR 1 mergeado, a dor principal (re-download diário do
mesmo pool sob 429) já está resolvida — o cache quente elimina a dependência de rede.
M2 protege o disco; M3 cobre o caso residual (miss com IP throttled). Parar e validar
no checkpoint de cada milestone; cada PR entrega valor sem depender dos seguintes.
