# Research: Downloads resilientes de backgrounds do YouTube

**Feature**: 002-youtube-download-resilience | **Date**: 2026-08-25

Sem `NEEDS CLARIFICATION` pendentes no Technical Context; as decisões abaixo
resolvem as escolhas em aberto. O item 1 foi verificado diretamente no pacote
instalado (`.venv/.../pytubefix`, versão 10.11.0), não em documentação externa.

## 1. Mecanismo de po_token no pytubefix 10.11.0 (verificado no código instalado)

**Decision**: passar `use_po_token=True` e
`po_token_verifier=lambda: (visitor_data, po_token)` ao construir
`YouTube(...)` quando o token estiver configurado; na construção do
`PyTubeProxy` com token presente, limpar o cache interno de tokens do pytubefix
(`pytubefix/__cache__/tokens.json`, via `pytubefix.helpers.reset_cache()` ou
remoção direta — confirmar na implementação qual dos dois limpa esse arquivo).

**Rationale** (fatos verificados no código de 10.11.0):

- Todos os clients configurados no projeto (`WEB`, `MWEB`, `WEB_SAFARI`) têm
  `require_po_token: True` em `innertube._default_clients`.
- Hoje, sem `use_po_token`, o pytubefix **já gera** um poToken automaticamente
  via botGuard (Node.js embutido por `nodejs_wheel`, que está instalado) —
  `YouTube.pot` → `bot_guard.generate_po_token(...)`. É esse token sintético que
  está tomando 429: ele não carrega uma sessão real de navegador. O token manual
  do operador substitui essa via: em `__main__.py`,
  `self.po_token = innertube.access_po_token or self.pot` — o token do verifier
  tem precedência sobre o botGuard.
- Com `use_po_token=True`, o `InnerTube` chama o `po_token_verifier` (que deve
  retornar a tupla `(visitorData, po_token)` nessa ordem) e injeta o token no
  corpo da requisição do player e como query param nas URLs de stream
  (`extract.apply_po_token`).
- **Armadilha encontrada**: o `InnerTube.__init__` com `use_po_token=True` e
  `allow_cache=True` (default; `YouTube` não expõe `allow_cache`) carrega
  `pytubefix/__cache__/tokens.json` se existir, e o verifier só é consultado se
  não houver token carregado; após consultar o verifier, ele regrava esse cache.
  Consequência: um token rotacionado no `.env` seria sombreado pelo token velho
  cacheado dentro do site-packages. Daí a decisão de limpar esse cache na
  construção do proxy quando um token está configurado — a config vira a única
  fonte de verdade e a renovação documentada não exige passos dentro do venv.
- `use_po_token`/`po_token_verifier` emitem warning de deprecação em 10.11.0
  ("will be removed soon"). Aceitável: a versão está pinada (`pytubefix==10.11.0`
  no pyproject). Se um upgrade futuro remover o parâmetro, o pin segura até a
  migração (registrado como risco; o proxy isola o ponto de contato em um único
  método).

**Obtenção manual do token (base da doc do operador)**: com um vídeo aberto no
YouTube no navegador (aba anônima recomendada, sessão sem login), DevTools →
Network → requisição `v1/player` → no payload,
`serviceIntegrityDimensions.poToken` e `context.client.visitorData`. Expiração se
manifesta como novos 429/403 em downloads mesmo com token configurado; renovação =
repetir a captura e atualizar o `.env`.

**Alternatives considered**:

- *OAuth do pytubefix* (`use_oauth`): amarra uma conta Google real ao pipeline e
  tem outro fluxo de expiração; risco para a conta e além do necessário.
- *Trocar para yt-dlp*: resolveria por outros meios, mas troca de biblioteca
  inteira está fora do escopo do spec e invalidaria as correções recentes.
- *Confiar no botGuard automático*: é o estado atual que está tomando 429; nada a
  fazer.
- *Proxy/rotação de IP*: explicitamente fora de escopo no spec.

## 2. Posição do cache na arquitetura

**Decision**: decorator `CachingYouTubeProxy(IYouTubeProxy)` que envolve o
`PyTubeProxy`, montado na `YouTubeProxyFactory`. `list_video_ids` delega direto;
`download_video` consulta o cache.

**Rationale**: o `VideoService` continua ignorando a existência de cache
(Princípio II); o decorator funciona sobre qualquer implementação futura da
interface; testável com um proxy interno fake, sem rede e sem pytubefix.

**Alternatives considered**:

- *Cache dentro do `PyTubeProxy`*: mistura acesso ao YouTube com armazenamento no
  mesmo módulo; testes do cache passariam a carregar pytubefix.
- *Cache no `VideoService`*: vaza detalhe de infraestrutura para a camada de
  serviço; viola a direção de dependência.

## 3. Layout, chave e metadados do cache

**Decision**: diretório plano; arquivo `{video_id}-{hq|lq}.mp4`; `mtime` como
"último uso" (touch com `os.utime` no hit); sem arquivo de índice.

**Rationale**: `video_id` já é validado com `[-_A-Za-z0-9]{11}` no proxy, então o
nome de arquivo é seguro; a faixa de qualidade entra na chave porque
`download_video(low_quality=True)` retorna outro conteúdo (edge case do spec);
o próprio filesystem carrega os metadados necessários (tamanho, mtime) — um
índice separado seria um segundo estado para manter consistente (Princípio I).

**Alternatives considered**: índice JSON/SQLite (estado duplicado, mais ramos de
reparo); subdiretórios por qualidade (equivalente, mais níveis sem ganho).

## 4. Atomicidade e validação de integridade

**Decision**: escrever em arquivo temporário no mesmo diretório e publicar com
`os.replace` (rename atômico no mesmo filesystem). No hit, validar barato:
arquivo legível, não vazio e com assinatura de container MP4 (box `ftyp` no
offset 4); falhou → apagar e tratar como miss.

**Rationale**: rename atômico elimina a classe inteira de "arquivo meio escrito
visível" (processo morto no meio do download, duas execuções concorrentes — a
última vence, nenhuma lê arquivo rasgado; leitura concorrente à eviction vira
`FileNotFoundError` → miss). A validação barata cobre truncamento e lixo
(FR-005) sem pagar um parse de vídeo por hit. Risco residual: corrupção que
preserva o header passa pelo cache e cai no skip já existente do
`VideoService` — aceito e documentado (o arquivo segue elegível à eviction; a
alternativa de parse completo custa mais que o download evitado).

**Alternatives considered**: checksum sidecar (segundo arquivo para manter em
sincronia); parse com moviepy no hit (custo desproporcional); lock files
(complexidade de concorrência que o rename atômico já resolve).

## 5. Limite de disco e eviction

**Decision**: cap configurável em GB (padrão 20), aplicado após cada escrita:
somar tamanhos, remover o menor `mtime` até caber. Hit faz touch, então reuso
conta como uso (LRU). Cap menor que o arquivo recém-gravado: o arquivo pode ser
evictado em seguida, mas os bytes já foram retornados — a execução corrente não
sofre.

**Rationale**: satisfaz FR-004 e os cenários da US2 com o mínimo de estado; o
pool atual (~144 shorts) cabe com folga em 20 GB, então eviction em produção é
exceção, não rotina.

**Alternatives considered**: TTL por idade (não limita tamanho, que é o
requisito); LFU (contador = estado extra; LRU por mtime é gratuito).

## 6. Superfície de configuração

**Decision**: não-segredos em `PyTubeYouTubeConfig` como modelo aninhado
`BackgroundCacheConfig {enabled: bool = True, dir: str =
"~/.cache/video-generator/backgrounds", max_gigabytes: float = 20.0}` (yaml em
`proxies.youtube_config.cache`); segredos em `Secrets` (`.env`):
`youtube_po_token`, `youtube_visitor_data`, injetados via container → factory,
como os demais segredos do projeto. Token em branco/whitespace = ausente.

**Rationale**: segue exatamente o padrão existente (`Secrets` com
`env_file=".env"`, que já está no `.gitignore`; factories recebem segredos e os
colocam na config do proxy). Caminho do cache com `~` expandido na construção;
padrão fora da working tree como pede FR-003.

**Alternatives considered**: cache config em `services.video_config` (o cache é
detalhe do proxy, não regra do serviço); token via arquivo apontado no yaml
(segundo mecanismo de segredo sem necessidade — `.env` já resolve).

## 7. Observabilidade de hit/miss (FR-007)

**Decision**: o decorator loga cada hit e cada miss em INFO com contadores
acumulados da instância (ex.: "cache hit para X (12 hits / 3 misses até agora)").

**Rationale**: o decorator não sabe onde uma "execução" começa e termina; como o
container cria um proxy por processo e cada run é um processo, os contadores da
instância são de fato "por execução" sem acoplar o service ao cache.

**Alternatives considered**: expor stats para o `VideoService` logar no fim
(acopla o service ao decorator, quebrando a transparência que motivou o
decorator).
