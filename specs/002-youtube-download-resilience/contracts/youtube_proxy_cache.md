# Contract: Decorator de cache sobre `IYouTubeProxy`

**Feature**: 002-youtube-download-resilience

## Invariante congelada

A interface `IYouTubeProxy` (`src/proxies/interfaces.py`) **não muda**:

```python
async def list_video_ids(url: str, surface: Literal["videos", "shorts"] = "videos") -> List[str]
async def download_video(video_id: str, low_quality: bool = False) -> bytes
```

O `VideoService` continua consumindo exatamente essa interface; testes devem
falhar se a assinatura mudar.

## Comportamento do `CachingYouTubeProxy`

Dado `CachingYouTubeProxy(inner: IYouTubeProxy, cache: BackgroundCacheConfig)`:

| # | Dado | Quando | Então |
|---|------|--------|-------|
| C1 | qualquer URL | `list_video_ids(...)` | delega ao `inner` sem tocar no cache |
| C2 | entrada válida para `(video_id, faixa)` | `download_video(...)` | retorna os bytes do arquivo; `inner` **não** é chamado; `mtime` atualizado |
| C3 | sem entrada | `download_video(...)` | chama `inner` uma única vez; retorna os bytes; persiste via tmp + `os.replace` |
| C4 | entrada ilegível/vazia/sem `ftyp` | `download_video(...)` | apaga a entrada e segue como C3 (miss) |
| C5 | `inner` levanta (inclusive `YouTubeRateLimitError`) em miss | `download_video(...)` | exceção propaga inalterada; nada é gravado |
| C6 | diretório não gravável / escrita falha | `download_video(...)` em miss | bytes do `inner` são retornados; warning logado; execução não falha |
| C7 | mesma `video_id`, faixas diferentes | dois downloads | duas entradas independentes (`-hq` / `-lq`) |
| C8 | soma dos tamanhos > cap após escrita | fim do `download_video` | remove entradas por menor `mtime` até caber; os bytes retornados não são afetados |
| C9 | duas execuções concorrentes no mesmo miss | ambas terminam | ambas retornam bytes válidos; a última escrita vence; nenhuma lê arquivo parcial |
| C10 | qualquer hit ou miss | — | log INFO com o evento e contadores acumulados (hits/misses) da instância |
| C11 | `cache.enabled = False` | montagem na factory | decorator não é montado; `PyTubeProxy` é usado direto (comportamento de hoje) |

## Contrato do po_token no `PyTubeProxy`

| # | Dado | Quando | Então |
|---|------|--------|-------|
| T1 | `po_token` e `visitor_data` configurados | qualquer download | `YouTube(...)` recebe `use_po_token=True` e `po_token_verifier` que retorna `(visitor_data, po_token)` — nessa ordem |
| T2 | nenhum token configurado (ou em branco) | qualquer download | `YouTube(...)` construído exatamente como hoje (nem `use_po_token`, nem verifier) |
| T3 | só um dos dois valores configurado | construção do proxy | erro imediato e claro (configuração pela metade) |
| T4 | token configurado | construção do proxy | cache interno de tokens do pytubefix é limpo (token rotacionado no `.env` nunca é sombreado) |
| T5 | download falha com token configurado | erro propagado | mensagem menciona possível expiração do token e aponta `docs/po-token.md` |
| T6 | 429 em qualquer cenário | download | `YouTubeRateLimitError` continua abortando imediatamente (FR-013) |
