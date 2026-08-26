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

- [X] T013 [P] [US3] Testes de contrato T1–T6 (construção do `YouTube` com/sem token, ordem da tupla do verifier, par incompleto→erro, mensagem de expiração, 429 preservado, limpeza do cache de tokens do pytubefix na construção) em tests/test_pytube_proxy.py

### Implementação

- [X] T014 [P] [US3] Adicionar `youtube_po_token` e `youtube_visitor_data` (Optional, default None) em src/core/secrets.py
- [X] T015 [US3] Adicionar `po_token`/`visitor_data` em `PyTubeYouTubeConfig` (nunca vindos do yaml; whitespace→None) em src/entities/configs/proxies/youtube.py, repassar de `Secrets` na `YouTubeProxyFactory.create` em src/proxies/factories.py e no wiring em src/core/container.py (mesmo padrão dos demais segredos)
- [X] T016 [US3] No `PyTubeProxy` (src/proxies/pytube_proxy.py): validar o par na construção (fail fast se incompleto, T3); limpar o cache interno de tokens do pytubefix quando token presente (T4 — `pytubefix.helpers.reset_cache()` ou remoção de `pytubefix/__cache__/tokens.json`, confirmar qual limpa o arquivo, ver research.md §1); passar `use_po_token=True` + `po_token_verifier` em `_download_with_client` (T1/T2); enriquecer o erro de download com a dica de expiração apontando docs/po-token.md quando token configurado (T5)
- [X] T017 [P] [US3] Escrever docs/po-token.md em português cobrindo os 4 pontos do contrato configuracao.md (o que resolve/não resolve, obtenção via DevTools `v1/player`, instalação no `.env`, reconhecimento de expiração e renovação)
- [X] T018 [P] [US3] Adicionar placeholders `YOUTUBE_PO_TOKEN`/`YOUTUBE_VISITOR_DATA` com comentário apontando docs/po-token.md em env.example, e referenciar a doc na seção YouTube de docs/configuration.md

### Live verification (milestone gate)

- [X] T019 [US3] Rodar `uv run pytest tests/test_pytube_proxy.py -q` (verde), a checagem de higiene `git grep -iE "po_token|visitor_data" -- ':!specs' ':!docs' ':!*.md'` (só nomes de campo, nenhum valor) e o cenário 🌐 manual do quickstart.md M3: seguir docs/po-token.md do zero, download passa com token; token corrompido → mensagem aponta a doc (SC-004/SC-005)

**Evidência do gate M3 (2026-08-25)**:

- Automatizado: `uv run pytest tests/test_pytube_proxy.py -q` → **20 passed** (9 novos, todos
  vermelhos antes da implementação). Regressão completa `uv run pytest -q` → **178 passed,
  1 failed** — a mesma falha pré-existente do T001 (`tests/test_translation_pipeline.py::test_pipeline`),
  intocada.
- Higiene de segredo (FR-009): `git grep -iE "po_token|visitor_data" -- ':!specs' ':!docs' ':!*.md'`
  (repetido com `grep -rIn` para pegar também os arquivos ainda não rastreados) → só nomes de
  campo, placeholders do `env.example` (`your_po_token_here`) e literais óbvios de teste
  (`THE-TOKEN`, `po-token-value`). **Nenhum valor real** em arquivo rastreado.
- Manual, contra o `config.yaml` real carregado por `MainConfig.from_yaml`, o
  `ApplicationContainer` real e o pytubefix real. O par sintético entrou por **variável de
  ambiente**, não no `.env` do usuário — nenhum token real foi capturado ou gravado:
  - Container montou `CachingYouTubeProxy` → `PyTubeProxy` com `use_po_token=True` e
    `po_token_verifier()` devolvendo `(visitor_data, po_token)` **nessa ordem** (T1/T2 ao vivo).
  - T4 ao vivo: um `tokens.json` obsoleto foi plantado em
    `.venv/.../pytubefix/__cache__/`; após construir o proxy com token, **o arquivo e o
    diretório sumiram** — `reset_cache()` é de fato quem limpa esse cache (a dúvida deixada
    em aberto na research.md §1 fica resolvida: `reset_cache()`, não remoção manual).
  - O `pytubefix.YouTube` **real** aceitou os kwargs (`yt.use_po_token is True`), então o
    contrato não depende do fake dos testes.
  - T3 ao vivo pela `YouTubeProxyFactory` real: par pela metade → `ValueError` citando
    `YOUTUBE_PO_TOKEN`, `YOUTUBE_VISITOR_DATA` e `docs/po-token.md`.
  - Valores em branco pela factory real → `config.po_token` normalizado para `None` e
    nenhum kwarg passado (comportamento idêntico ao de hoje, FR-010).
- 🌐 Manual, SC-005 (segunda metade — token corrompido): download real de `NC7t39glF1U` com
  um par inválido, pelo `PyTubeProxy` interno (cache propositalmente contornado). O YouTube
  respondeu **HTTP 429 de verdade**; o proxy abortou depois de **um único cliente** (FR-013 /
  T6 ao vivo) e a mensagem trouxe a dica: *"A po_token is configured, so it may have expired —
  ... see docs/po-token.md to renew it."* (T5 ao vivo).
- Caminhos de campo da doc conferidos contra o próprio pytubefix: `insert_po_token` grava o
  token em `serviceIntegrityDimensions.poToken` e o visitor em `context.client.visitorData` —
  exatamente os dois campos que `docs/po-token.md` manda ler do payload de `v1/player`.
- **Não verificado ao vivo** (rótulo corrigido no T020: isto **não** é o SC-004 — o SC-004 do
  spec é "sem token configurado, comportamento inalterado", que *está* verificado logo acima
  e pela regressão verde; o que falta abaixo é a segunda metade do cenário manual do SC-005):
  um token **válido** fazendo um download passar. Exige
  capturar um po_token real da sessão de navegador do operador e instalá-lo no `.env`, o que é
  ação dele, não do agente; e com o IP em 429 o resultado não seria conclusivo de qualquer
  forma (a própria doc registra que o token não resgata um IP já bloqueado). O caminho até o
  pytubefix está verificado ponta a ponta com token sintético; o que falta é só a aceitação
  pelo YouTube. Também não verificado: a leitura da doc por um operador humano do zero.

**Checkpoint**: ✅ Milestone 3 DONE (2026-08-25, gate passou) — PR 3 abre aqui.

---

## Phase 6: Polish & Cross-Cutting

- [X] T020 Rodar a regressão completa `uv run pytest -q` e revisar os artefatos da feature (spec/plan/contratos) marcando divergências que a implementação revelou
- [X] T021 [P] Atualizar o checklist specs/002-youtube-download-resilience/checklists/requirements.md se algum requisito mudou durante a implementação

**Evidência do T020 (2026-08-25)**:

- Regressão completa: `uv run pytest -q` → **178 passed, 1 failed**. A única falha é a
  pré-existente registrada no T001 (`tests/test_translation_pipeline.py::test_pipeline`,
  `PromptLLMProxy.translate_and_adapt` não existe mais), alheia à feature e intocada por ela.
- Invariantes congelados conferidos no git: `src/proxies/interfaces.py` e
  `src/services/video_service.py` **não foram tocados** por nenhum dos três commits da
  feature (`b1016fa`, `fea6e7e`, `9ab832b`) — o último commit neles é `3ad4c90`, anterior
  ao M1. A `IYouTubeProxy` seguiu congelada como o contrato exige.
- Cobertura dos contratos: C1–C11 e T1–T6 têm teste nomeado correspondente
  (34 em tests/test_caching_youtube_proxy.py, 20 em tests/test_pytube_proxy.py),
  incluindo C9 (`test_concurrent_misses_both_return_valid_bytes`) e a atomicidade
  (`test_no_partial_file_is_visible_while_the_download_runs`).

**Divergências encontradas e marcadas nos artefatos**:

1. **research.md §1** — a decisão deixava em aberto "confirmar na implementação qual dos
   dois limpa esse arquivo" (`reset_cache()` vs. remoção manual de `tokens.json`).
   Resolvido: é o `reset_cache()`, verificado ao vivo no gate do M3. Marcado na research.md.
2. **plan.md, gate do M3** — citava "FR-004 do spec de qualidade" para a higiene de segredo;
   o FR correto é o **FR-009** (FR-004 é o cap de disco). Referência corrigida no plan.md.
3. **tasks.md, evidência do M3** — o item "não verificado ao vivo" estava rotulado como
   SC-004. O SC-004 do spec é "sem token configurado, comportamento inalterado", que *está*
   verificado (factory real normalizando valores em branco + regressão verde). O que segue
   sem verificação ao vivo é a segunda metade do cenário do **SC-005** (token válido fazendo
   um download real passar). Rótulo corrigido na própria evidência do M3.
4. **quickstart.md, pré-requisitos** — mandava `uv sync`, que **remove** o pytest (ele vive
   no extra `dev`), deixando a suíte impossível de rodar. Corrigido para `uv sync --extra dev`.
5. **quickstart.md, gate automatizado do M2** — o seletor `-k evict` alcança só 4 dos 9
   testes do M2 (ordem LRU, touch no hit e cap menor que um clipe não têm "evict" no nome),
   ou seja, não cobria o C8 inteiro que o guia prometia. Alinhado ao comando do T012
   (arquivo inteiro).
6. **quickstart.md, cenários manuais 🌐** — apontam para `tests/script_youtube_download.py`,
   que está defasado e **não serve** para validar a feature: chama `list_video_ids` de forma
   síncrona (a interface é `async` desde antes desta feature) e sorteia um `video_id` novo a
   cada execução, então a segunda rodada nunca produziria hit. Marcado no quickstart.md.
   Consertar o script continua fora do escopo desta feature.

**O que segue sem verificação (aceito, registrado)**:

- **SC-006** (≥80% menos tempo na aquisição de backgrounds com cache quente) — **não medido**.
  Os gates M1/M2 rodaram com um clipe local fazendo as vezes de rede no miss porque o IP
  esteve em 429 o dia todo, então os tempos observados não representam a economia real.
  Fica pendente de uma medição numa execução real com rede saudável; nada no código depende
  disso, é aferição.
- Segunda metade do **SC-005**: um po_token **válido** fazendo um download real passar —
  depende de o operador capturar um token da própria sessão de navegador, e com o IP em 429
  o resultado não seria conclusivo. O caminho até o pytubefix está verificado ponta a ponta
  com token sintético.
- **SC-005**, leitura da doc por um operador humano do zero — os caminhos de campo de
  `docs/po-token.md` foram conferidos contra o código do pytubefix, mas ninguém além do
  agente seguiu o passo a passo.

**Evidência do T021 (2026-08-25)**: nenhum requisito (FR-001–FR-013) ou critério de sucesso
(SC-001–SC-006) mudou de texto ou de intenção durante a implementação — as 6 divergências
acima são de artefato de apoio (comandos, referências cruzadas, uma pergunta em aberto da
research), não de requisito. O checklist segue 16/16 e ganhou apenas uma nota registrando
esta revisão pós-implementação.

### Verificação no servidor de produção (2026-08-26)

Rodada a pedido do usuário, depois do polish, com a feature **deployada de fato**
(`just deploy`, `video-bot.service` reiniciado no código novo). Fecha as lacunas que os
gates locais não conseguiram fechar.

**Bug real encontrado — só o servidor poderia pegá-lo**: `uv run --extra test pytest`
no Linux deu **1 failed, 122 passed** —
`test_eviction_tolerates_an_entry_another_run_already_removed` evictou o clipe errado.
Causa medida no próprio servidor: no ext4 dele, arquivos escritos no mesmo tick do
relógio recebem `st_mtime_ns` **idêntico** (o kernel serve um relógio grosseiro em
cache), enquanto o APFS do macOS dá timestamps distintos em nanossegundos. Com mtimes
iguais, a eviction caiu no desempate por nome e `survivorb22` ordena antes de
`vanishedaa1`. **O comportamento de produção está correto** — downloads reais ficam
segundos um do outro, então o mtime sempre os distingue; o teste é que assumia ordenação
natural, ao contrário dos outros testes de ordem, que já usavam `set_age`. Corrigido com
`set_age` explícito. Depois da correção o servidor dá **178 passed, 1 failed** — idêntico
ao local, a falha sendo a mesma pré-existente do T001.

**FR-007 e FR-013 observados em produção real** (journal do `video-bot.service`, 26/08):

- 07:14:56 e 07:34:06 — miss logado com contadores, depois `429` real: o proxy desistiu
  **sem tentar os outros clients** ("giving up on ... without trying the rest"). FR-013 ao
  vivo, no pipeline de verdade, não em teste.
- 07:43:40–07:43:59 — a mesma rodada passou e baixou **3 clipes reais** (65,5 MB), cada
  miss logado com os contadores acumulados. Os logs de hit/miss do FR-007 são exatamente
  o que o operador vê no journal.

**SC-001 e SC-006 — finalmente medidos com rede real** (o que faltava desde o M1):

- Baseline frio: **19,3 s** para os 3 clipes, tirado do journal do próprio bot
  (07:43:40 → 07:43:59), rede real, sem simulação.
- Rodada quente, instância nova de `CachingYouTubeProxy` sobre um `PyTubeProxy` **real**
  instrumentado para gritar em qualquer download: **0,195 s** (0,010 / 0,010 / 0,008 s).
- **SC-001: PASS** — 0 requisições de download, 3 hits / 0 misses, bytes idênticos aos do
  disco nos três clipes (sha256 conferido).
- **SC-006: PASS — 99,0% menos tempo** (19,3 s → 0,195 s), muito acima dos 80% exigidos.
  65,5 MB servidos do disco em vez da rede.

**po_token instalado no servidor** (par fornecido pelo usuário, gravado só no `.env`,
modo 600, nunca em arquivo rastreado):

- Wiring confirmado ponta a ponta: `.env` → `Secrets` (116 / 520 chars) → container →
  factory → `PyTubeYouTubeConfig` → `PyTubeProxy`, com `CachingYouTubeProxy` por fora.
- **O par chega ao corpo real da requisição do InnerTube**, byte a byte igual ao `.env`
  (`context.client.visitorData` e `serviceIntegrityDimensions.poToken`) — verificado sem
  gastar download.
- **T5 ao vivo com token real**: o 429 trouxe a dica de expiração apontando
  `docs/po-token.md`.
- **Observação para a doc**: o client WEB do pytubefix 10.11.0 se identifica como
  `2.20251021.01.00`, enquanto o token foi capturado de um navegador em
  `2.20260824.10.00`. Não foi possível provar que essa defasagem atrapalha, mas é
  candidata a explicar rejeições de token e vale registrar na renovação.

**O que continua sem verificação**: a segunda metade do **SC-005** — que um po_token
válido *faça um download passar*. O par estava configurado nas três tentativas (07:14 e
07:34 tomaram 429; 07:43 passou), então **não dá para distinguir** se o sucesso das 07:43
veio do token ou do fim da janela de throttle. O que está provado é que o token é
transmitido corretamente; a atribuição do resultado, não. Também segue não verificada a
leitura da doc por um operador humano do zero.

**Checkpoint**: ✅ Feature completa — os 3 milestones passaram seus gates, o polish fechou
e a verificação em produção confirmou SC-001 e SC-006 com rede real.

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
