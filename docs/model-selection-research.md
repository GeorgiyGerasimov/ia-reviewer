# Model selection for ia-reviewer

> **Дата отчёта:** июнь 2026. Ландшафт LLM меняется кварталами — числа ниже актуальны на момент написания.
> **Источник эмпирики:** наш собственный кейс [`gpt-oss-20b-2026-06-05.md`](observed-quality-cases/gpt-oss-20b-2026-06-05.md) плюс публичные бенчмарки 2026 года (см. раздел Sources в конце).
> **Адресат:** оператор ia-reviewer, выбирающий модель для каждой из пяти ролей в графе (`Validator`, три `*Reviewer`, `Judge` для inline-gate / offline-CLI, `ExploitProposal`).

## TL;DR

| Роль | Single H200 (141 GB) — или меньше | Multi-GPU (≥200 GB) | Через gateway |
|---|---|---|---|
| **Validator** | Mistral Small 3 (24B) | n/a | Sonnet 4.6 |
| **Reviewer** (горячий путь, per-file) | **Qwen3.6-27B BF16 / FP8** (см. §4.1) | gpt-oss-120b, DeepSeek V3 | **Sonnet 4.6** (наш текущий default) |
| **Judge** (inline gate + CLI) | Qwen3.6-27B в thinking-mode (с осторожностью) | DeepSeek V3, Qwen3-235B | **Opus 4.6** (стронгер reviewer'а) |
| **ExploitProposal** | Qwen3.6-27B thinking-mode как fallback; иначе через gateway | DeepSeek V3, Kimi K2.6 | **Opus 4.6** или GPT-5.3 Codex |

**Граница «полезный vs шум» по нашим критериям:** ≥27B параметров **с современной hybrid-attention архитектурой** (Qwen3.6-27B, апрель 2026) ИЛИ ≥30B с code-fine-tune ИЛИ ≥100B общего назначения. Всё ниже — повторение опыта gpt-oss-20b (12% signal-to-noise, 13% галлюцинированных файлов, фабрикация CVE).

> **Историческая правка (июнь 2026).** Эта рекомендация изменилась после релиза Qwen3.6-27B (22 апреля 2026), который выбил **77.2% на SWE-bench Verified** в 27B-dense архитектуре — это уровень между Claude Sonnet 4.6 (79.6%) и Gemini 3.1 Pro (75%). До этого Tier-S рекомендация была Qwen3-Coder-32B (58.7% SWE-bench). См. §4.1 для деталей миграции.

---

## 1. Где проходит граница полезности

### 1.1 Наша анкорная точка — gpt-oss-20b (июнь 2026)

Self-review проекта моделью `gpt-oss-20b` ([детали](observed-quality-cases/gpt-oss-20b-2026-06-05.md)) дал 77 findings, из которых:

- **~12% true positives** — и все эти 5 находок уже были задокументированы в нашем `security-checklist.md` как known/out-of-scope, то есть **новой полезной информации = 0**
- **20% misread** — модель реагировала на токен-уровневые паттерны (`f-string`, `subprocess`) без чтения соседних строк, которые инвалидировали вывод
- **13% галлюцинированных файлов** — `src/auth.py`, `src/server.py`, `src/main.py` и т.д., в проекте отсутствующих
- **49% дубликатов** — одна и та же находка пересказана разными словами
- **1 fully fabricated exploit PoC**, демонстрировавший SQLi на функции, которой в коде нет

Это нижний порог: **20B параметров без code-specialised fine-tune непригодны** для security-обзора нашего класса задач. Подтверждается публичной статистикой: gpt-oss-20b имеет ~23% hallucination rate, gpt-oss-120b — ~25% ([modelslab.com/llm-hallucination-rates-2026](https://modelslab.com/blog/llm/llm-hallucination-rates-2026)).

### 1.2 Какие свойства нужны от модели для роли `Reviewer`

Из устройства нашего графа (см. `CLAUDE.md::Graph topology`):

1. **Per-file LLM calls** в repo-mode (от 30 до `MAX_FILES_PER_AGENT=200` вызовов на одного агента) — латентность критична, длинный контекст не требуется
2. **Strict-JSON output** — `BaseReviewer._parse_response` парсит структуру; неаккуратный JSON = тихий drop finding'а
3. **Хорошее instruction-following** на негативных правилах — «не выдумывай CVE» работает только если модель умеет уважать ограничения промпта
4. **Понимание security-паттернов** — SQLi на `$N` placeholders (asyncpg) НЕ должен срабатывать, как у gpt-oss-20b

Из бенчмарков-критериев:
- **SWE-bench Verified ≥ 50%** — нижняя планка для production. У моделей <30B обычно <40%
- **HumanEval ≥ 80%** — базовое чтение кода
- **MMLU-Pro ≥ 70%** — общее ризонинг-качество (для отделения dev-defaults от prod-багов)
- **Long-context ≥ 64K** — для одного файла среднего проекта хватает; 200K+ — bonus для repo-mode дедупа

### 1.3 Где utility начинает перевешивать

| Размер | Code SWE-bench | Hallucination | Вердикт для ia-reviewer |
|---|---|---|---|
| <20B без code-FT | <30% | 23-30% | **Не использовать** — повтор кейса gpt-oss-20b |
| 22-32B classic dense с code-FT (Qwen3-Coder, Codestral) | 55-65% | 15-20% | **Минимально пригодно** для PR-mode + дешёвых ролей |
| **27B hybrid-attention (Qwen3.6-27B, апр 2026)** | **77.2%** | **est. 10-15%** | **Production-grade** — новый Tier-S default |
| 70B classic dense (Llama 3.3, Qwen 72B) | 65-75% | 12-18% | **Solid baseline** — пригодно для всех ролей |
| 100-150B MoE (gpt-oss-120b, Llama 4 Scout) | 70-80% | 15-25% | **Production-grade** — основная рекомендация |
| 200B+ MoE (DeepSeek V3 671B, Qwen3-235B) | 80-87% | 10-15% | **Frontier open-weights** — рядом с Sonnet 4.6 |
| 1T MoE (Kimi K2.6) | ~84% | ~10-15% | **Frontier open** — требует ≥8× H200 (см. §4.3.1) |
| Proprietary (Sonnet/Opus 4.6, GPT-5.x, Gemini 3.x) | 75-85% | 8-15% | **Frontier closed** — текущий default |

> **Архитектура вне зависимости от размера.** Qwen3.6-27B показывает, что **hybrid Gated DeltaNet + Gated Attention** перебивает чисто dense-классику. На 27B параметров эта архитектура держит уровень frontier moderate-MoE. Это означает, что table выше — по размеру — теперь неполная: важна не только величина, но и тип внимания. Hybrid-architectures стоит проверять как отдельный класс.

---

## 2. Сегментация по железу

### 2.1 Tier S — одна GPU (24-141 GB)

После Qwen3.6-27B (апрель 2026) определение Tier S расширилось вниз — топовая открытая модель для code review теперь помещается на **consumer-grade GPU** (RTX 5090, 32GB). Раньше Tier S = «1× H200» по бюджету; сейчас Tier S = «одна любая GPU от 24GB».

| Модель | Размер | Тип | BF16 | FP8 | Q4_K_M |
|---|---|---|---|---|---|
| **Qwen3.6-27B** ⭐ | 27B dense + hybrid attention | code/agentic | **54 GB** → 1× H100 / H200 | **27 GB** → L40S, RTX 6000 Ada | **14 GB** → RTX 4090, RTX 5090 |
| Qwen3-Coder-32B (legacy) | 32B dense | code-FT | 64 GB → 1× A100/H100 | 32 GB → L40S | 16 GB → RTX 4090 |
| Codestral 22B | 22B dense | code FIM | 44 GB → 1× A100 | 22 GB → RTX 6000 Ada | 11 GB → RTX 4090 |
| Mistral Small 3 (24B) | 24B dense | general | 48 GB → 1× A100 | 24 GB | 12 GB |
| Llama 3.3 70B | 70B dense | general | 140 GB → 1× H200 | 70 GB → 1× H100/H200 | 35 GB → L40S |
| Qwen 2.5 72B / Qwen3 72B | 72B dense | general | 144 GB → multi-GPU | 72 GB → 1× H200 | 36 GB → L40S |
| gpt-oss-120b | 117B total, 5.1B active | MoE Apache 2.0 | 234 GB → multi-GPU | **117 GB → 1× H200** | 58 GB → 1× H100 |
| Llama 4 Scout | 109B total, 17B active | MoE | 218 GB → multi-GPU | 109 GB → 1× H200 | 54 GB → 1× H100 |

⭐ = новый Tier-S default после апреля 2026.

**FP8 — рекомендуемая квантизация** для code-задач: -0.3 до -0.5 балла MMLU-Pro vs FP16 (в пределах run-to-run шума). AWQ 4-bit стоит -1.4 до -1.8 балла на code-бенчмарках, INT4 для code лучше избегать ([digitalapplied.com](https://www.digitalapplied.com/blog/quantization-tradeoffs-4bit-8bit-fp8-performance-data)). Для Qwen3.6-27B специально измерили — HumanEval падает только с 86% (BF16) до 84% (Q4_K_M) — то есть на Q4 потери минимальны даже на code.

### 2.2 Tier M — 2-4× H100/H200 (160-560 GB)

Открывает frontier-class open-weights:

| Модель | Размер | Comments |
|---|---|---|
| Qwen3-235B-A22B-Reasoning | 235B total, 22B active | Топ-3 open-weights на reasoning бенчмарках |
| Llama 4 Maverick | ~400B total, ~17B active | MoE, 1M context |
| DeepSeek V3 (FP8) | 671B total, 37B active | Лидер open-weights coding (SWE-bench 83.7%) |
| Kimi K2.6 | многомиллиардный MoE | Топ open-source agentic coding |

### 2.3 Tier L — 8× H200 (1128 GB)

Для DeepSeek V3 / V4 full precision: 8× H200 ≈ $36/hr на cloud-провайдерах ([spheron.network](https://www.spheron.network/blog/gpu-requirements-cheat-sheet-2026/)). Это уже не «локальная» инсталляция — production-deploy уровня корпоративного кластера.

#### 2.3.1 MoE memory math — типичное заблуждение

Часто видишь утверждение: «Kimi K2.6 (1000B total, 32B active) — да это же дёшево, всего 32B надо в память!». **Это неверно.** Active params влияют только на FLOPs/токен (compute), а в VRAM нужно держать **все** параметры:

- Router на каждом токене выбирает 2-8 экспертов из десятков
- Никогда заранее не известно, какой эксперт сработает
- Swap эксперта из CPU/disk вместо VRAM = 100-1000× латентность

Математика по квантизациям для Kimi K2.6 (1000B total):

| Quant | Веса | + KV/activations (~20%) | **Realistic VRAM** | GPU setup |
|---|---|---|---|---|
| FP16 | 2000 GB | +400-500 GB | **~2400 GB** | 17× H200 / 13× B200 / 1× NVL72 rack |
| FP8 | 1000 GB | +200-250 GB | **~1200 GB** | **9× H200** / 7× B200 |
| AWQ 4-bit | 500 GB | +100-150 GB | **~650 GB** | **5× H200** |
| Q3 GGUF | 420 GB | +80-120 GB | **~500 GB** | **4× H200** (впритык) |
| Q2 GGUF | 300 GB | +60-90 GB | **~390 GB** | **3× H200** (потеря качества) |

Сравнение **memory footprint vs latency** для frontier open-weights:

| Модель | Total / Active | VRAM FP8 | GPU setup FP8 | Latency tier |
|---|---|---|---|---|
| **Kimi K2.6** | 1000B / 32B | ~1200 GB | 8-9× H200 | как dense-32B (быстрая) |
| DeepSeek V3 | 671B / 37B | ~800 GB | 6-8× H200 | как dense-37B |
| Qwen3-235B-A22B | 235B / 22B | ~280 GB | 2-3× H200 | как dense-22B |
| Llama 4 Maverick | ~400B / 17B | ~480 GB | 4× H200 | как dense-17B |
| gpt-oss-120b | 117B / 5.1B | ~140 GB | **1× H200** | как dense-5B (super fast) |

**Главный инсайт:** MoE даёт «качество как у full-size модели, latency как у active-size». Платишь памятью. Поэтому MoE адекватна когда:
- Уже есть production-кластер с большим объёмом VRAM
- Объём reviews большой, и latency на сотрудника-ревьюера важна сильнее, чем количество одновременных reviews

### 2.4 Сравнение FP16 / FP8 / Q4 для нашего use-case

| Quant | VRAM/param | Loss on HumanEval | Подходит для |
|---|---|---|---|
| FP16 | 2 байта | baseline | reference, экспериментальные сравнения |
| FP8 | 1 байт | -0.3 до -0.5 (шум) | **рекомендуется для всех ролей** |
| Q5_K_M (GGUF) | ~0.7 байта | -2% | приемлемо для validator / chat |
| AWQ 4-bit | 0.5 байта | -1.4 до -1.8 баллов | приемлемо для validator, **не для reviewer** |
| GPTQ 4-bit | 0.5 байта | -1.5 до -2 балла | то же |
| INT4 | 0.5 байта | значительный | **не использовать для code** |

---

## 3. Proprietary frontier (через AI Gateway)

Сравнение лидеров на момент июня 2026:

| Модель | SWE-bench Verified | Strength | Цена in/out per 1M | Context |
|---|---|---|---|---|
| **GPT-5.3 Codex** | 85% | terminal/DevOps/security | ~$5/$25 | 256K |
| GPT-5.4 | 84% | general-purpose | ~$3/$15 | 256K |
| **Claude Opus 4.6** | 80.8% | logic bugs, multi-file ризонинг | ~$15/$75 | 200K (1M beta) |
| **Claude Sonnet 4.6** | 79.6% | balanced, наш default | $3/$15 | 200K (1M beta) |
| Gemini 3.1 Pro | 75% | дешёвый, гигантский context | $2/$12 | 1M production |

Источники: [mindstudio.ai](https://www.mindstudio.ai/blog/gpt-54-vs-claude-opus-46-vs-gemini-31-pro-benchmarks), [aimagicx.com](https://www.aimagicx.com/blog/claude-opus-4-6-vs-gpt-5-4-vs-gemini-3-1-benchmark-comparison-april-2026).

**Для ia-reviewer:** Sonnet 4.6 — это правильный default. Он:
- Сидит ровно над «production-grade» порогом (SWE-bench 79.6%)
- Цена/качество лучше Opus в 5× для большинства ролей
- Опус оставляем для `JUDGE_MODEL` (см. §5) и для `ExploitProposalAgent` (низкий объём вызовов, высокая важность одного правильного ответа)

---

## 4. Open-weights tier — детальные рекомендации

### 4.1 Single H200 (или менее) — рекомендация по умолчанию

**Qwen3.6-27B BF16 или FP8.** После релиза в апреле 2026 это — новый Tier-S default, заменивший прежнюю рекомендацию Qwen3-Coder-32B.

Почему именно эта модель:

- **SWE-bench Verified 77.2%** — выше Gemini 3.1 Pro (75%), почти на уровне Claude Sonnet 4.6 (79.6%). Это **в 1.3× выше прежнего рекомендованного Qwen3-Coder-32B** (58.7%)
- **27B dense** — fits в 54 GB BF16 / 27 GB FP8 / 14 GB Q4_K_M. Полная свобода в выборе железа от RTX 5090 до H200
- **Hybrid Gated DeltaNet + Gated Attention** — архитектурная новация, которая позволяет 27B играть в лиге frontier 100B+
- **262K context native** (до 1M с YaRN scaling) — для repo-mode с 200 файлами хватит с огромным запасом
- **Hybrid thinking** — переключаемый reasoning mode в одном чекпойнте (аналог Claude extended-thinking). Можно использовать в `ExploitProposal` роли где нужен максимум reasoning
- **Apache 2.0** — лицензия совместима с PolyForm NC основного проекта
- **Multimodal** (text + image + video) — для нашего use-case не критично, но возможно полезно для future code-screenshot-в-PR анализа

Quality loss на квантизации (специально измеренное для Qwen3.6-27B):
- BF16 → HumanEval **86%**
- Q4_K_M → HumanEval **84%** (−2 балла, минимально)

**Альтернативы и fallback'и:**

| Сценарий | Модель | Почему |
|---|---|---|
| Runtime не поддерживает hybrid attention (Gated DeltaNet) | Qwen3-Coder-32B FP8 | Классическая dense архитектура, поддерживается везде |
| Нужно ещё дешевле / в IDE | Codestral 22B | Fill-in-middle оптимизация, специально для autocomplete |
| Нужен больший general-purpose ризонинг | Llama 3.3 70B FP8 | Выше MMLU, но 2× медленнее и проигрывает на code |
| Большой context (>262K) без YaRN | Llama 4 Scout (Q4) | 1M context native, MoE |

### 4.2 gpt-oss-120b — отдельный кейс

OpenAI выпустила gpt-oss-120b в августе 2025 под Apache 2.0 ([benchlm.ai](https://benchlm.ai/models/gpt-oss-120b)). 116.8B параметров, 5.1B активных (MoE), помещается на одной H100 80GB в FP4. На H200 — комфортно в FP8.

Бенчмарки:
- MMLU-Pro 90.0% — выше GLM-4.5 (84.6%), Qwen3 Thinking (84.4%), DeepSeek R1 (85.0%), Kimi K2 (81.1%)
- GPQA Diamond 80.9% — на уровне OpenAI o4-mini (81.4%)

**НО** — публичная статистика hallucination rate 25% ([modelslab.com](https://modelslab.com/blog/llm/llm-hallucination-rates-2026)), что выше чем у Qwen3-Coder-32B на code-специфичных задачах. **Для нашего use-case Qwen3-Coder-32B остаётся более предсказуемым выбором** на одной железке, gpt-oss-120b — для случаев где важнее общий ризонинг (например `Judge` или `ExploitProposal`).

### 4.3 Multi-GPU — production-grade

**DeepSeek V3** — лидер по SWE-bench Verified (83.7%) среди open-weights, на одном уровне с GPT-5.4 (84%). Требует 8× H200 в полной точности или 4× в FP8. Цена inference на cloud ~$0.04 за 1M токенов через сервис-провайдеров — дешевле любого proprietary.

**Qwen3-235B-A22B-Reasoning** — 22B активных параметров делают inference дешёвым. Топ-3 на open-weights leaderboards. Apache 2.0.

**Kimi K2.6** — лидер open-source по agentic coding согласно [benchlm.ai](https://benchlm.ai/blog/posts/best-open-source-llm) (84/100, при этом 3.6× дешевле Opus). Сильна на complex end-to-end tasks с tool calls — наш случай.

### 4.4 Чего избегать

| Модель | Размер | Почему не подходит |
|---|---|---|
| gpt-oss-20b | 20B | **Доказано** не работает на нашей задаче |
| Llama 3.1 8B | 8B | Слишком мала для security-задач |
| Mistral 7B | 7B | То же |
| Любая INT4 quantization для Reviewer | — | Code degradation значителен |
| Любая модель без published code-benchmarks | — | Нет данных для калибровки |

---

## 5. Рекомендации по ролям в графе ia-reviewer

Наш граф (см. `CLAUDE.md::Graph topology`) использует LLM в **пяти ролях**. Они имеют разный профиль и допускают разные модели — текущая инфраструктура поддерживает per-роль override через `model_name` constructor arg + env (`JUDGE_MODEL`, `REPORT_FORMATTER_MODEL`).

### 5.1 Validator (`src/agents/validator.py`)

- **Задача:** простой judge — accept / reject входящий запрос
- **Объём:** 1 вызов на review
- **Требования:** строгий JSON, минимальный ризонинг
- **Рекомендация:** Mistral Small 3 (24B) или Phi-4 14B — этого хватает. Текущий Sonnet 4.6 — overkill, но безвреден

### 5.2 Reviewer ×3 (`dependency`, `injection`, `owasp`)

- **Задача:** core security review
- **Объём:** в PR-mode = 1-3 вызова, в repo-mode = до 200 вызовов на агента
- **Требования:** strict-JSON, code understanding, instruction-following на негативных правилах
- **Рекомендация:**
  - **Single GPU self-host (24-141 GB):** **Qwen3.6-27B** (BF16 на H100/H200, FP8 на L40S, Q4_K_M на RTX 5090)
  - **Multi-GPU self-host:** DeepSeek V3 (FP8 on 4×H200)
  - **Через gateway:** Sonnet 4.6 (наш текущий выбор) — оптимум cost/quality
- **До замены default'а:** прогнать `benchmarks/dependency` (100% обязательно) + `benchmarks/judge-formatter` с другой моделью как судьёй + self-review проекта (как мы делали с gpt-oss-20b). Если signal-to-noise >50%, переключаем default.

### 5.3 LLMJudge (`src/evals/llm_judge.py`) — две поверхности

- **Поверхность А (`judge_report` CLI):** offline-grading финальных отчётов
- **Поверхность B (`FORMATTER_JUDGE_CHECK`):** inline-gate в графе формателя
- **Принцип:** judge ДОЛЖЕН быть стронгер reviewer'а — он ловит ошибки reviewer'а, а не дублирует их
- **Объём:** 1 вызов на review (опционально)
- **Рекомендация:**
  - Если reviewer = Sonnet 4.6 → **judge = Opus 4.6** (через `JUDGE_MODEL=claude-opus-4-6`)
  - Если reviewer = Qwen3-Coder-32B → **judge = gpt-oss-120b** или DeepSeek V3 на multi-GPU
  - Если reviewer = Opus 4.6 → **judge = GPT-5.3 Codex** для cross-vendor diversity

### 5.4 ReportFormatter (`src/agents/report_formatter.py`)

- **Задача:** prose polish — переписать TL;DR, не выдумывая
- **Объём:** 1 вызов на review (опционально)
- **Требования:** instruction-following ВЫШЕ обычного — каждое нарушение "не выдумывай файл" виден
- **Рекомендация:** **минимум Sonnet 4.6**, лучше Opus 4.6. Маленькие модели здесь категорически противопоказаны: galлюцинация в TL;DR — это именно то, для чего мы построили `FORMATTER_JUDGE_CHECK` gate

### 5.5 ExploitProposal (`src/agents/exploit_proposal.py`)

- **Задача:** генерация PoC + human-gated approval
- **Объём:** до `MAX_EXPLOIT_PROPOSALS=3` вызовов на review
- **Требования:** **максимальный ризонинг** — PoC должен быть синтаксически правильным, defensive-only по политике, не выдумывать функции
- **Рекомендация:** Opus 4.6 или GPT-5.3 Codex (последняя имеет специальный security-fine-tune)

---

## 6. Cost / latency матрица

Оценка месячного бюджета на ~1000 reviews/месяц (среднее: 1 validator + 50 reviewer + 1 formatter + 1 judge + 2 exploit = 55 вызовов на review):

| Setup | Месячный cost | Latency / review | Self-hosted | Privacy |
|---|---|---|---|---|
| Всё через Sonnet 4.6 (наш default) | ~$200-400 | 30-60 sec | ❌ | данные у Anthropic |
| Sonnet 4.6 reviewer + Opus 4.6 judge | ~$250-450 | 35-65 sec | ❌ | данные у Anthropic |
| **Qwen3.6-27B на 1× L40S** (cloud $1/hr) | **~$720 + Sonnet judge** | **15-30 sec** | ✅ reviewer | данные только judge у proprietary |
| **Qwen3.6-27B на 1× RTX 5090** (owned hardware) | **$0 marginal** | 15-30 sec | ✅ | полная |
| Qwen3.6-27B на 1× H200 (cloud $3.5/hr) | ~$2520 + Sonnet judge | 10-20 sec | ✅ reviewer | данные только judge у proprietary |
| Qwen3-Coder-32B на 1× H200 (legacy) | ~$2200 + Sonnet judge | 20-40 sec | ✅ reviewer | данные только judge у proprietary |
| DeepSeek V3 на 8× H200 (cloud $36/hr) | ~$26000 | 15-30 sec | ✅ | полная |
| Kimi K2.6 на 8× H200 FP8 (cloud $36/hr) | ~$26000 | 15-30 sec | ✅ | полная |
| DeepSeek V3 через DeepSeek API | ~$50-100 | 25-50 sec | ❌ | данные у DeepSeek (юрисдикция) |

**Главный инсайт (обновлено после Qwen3.6-27B):** баланс сместился. С Qwen3.6-27B на L40S/RTX железе **self-host точка безубыточности** против Sonnet 4.6 теперь ~1500-3000 reviews/мес (вместо прежних 5000+). Для типичной internal-команды (~500-1000 reviews/мес) proprietary всё ещё дешевле в деньгах, но разрыв сократился.

Self-host имеет смысл когда:
- Privacy / compliance запрещают отправку кода в proprietary API (главный driver)
- Уже есть H100/H200/L40S для других задач — marginal cost = $0
- Объём 1500-3000+ reviews/мес
- Хочется детерминистических costs (нет per-token-billing неопределённости)

---

## 7. Decision rubric

Когда выбираешь модель для роли в ia-reviewer:

```
1. SWE-bench Verified ≥ 50%?      → если нет, не использовать для Reviewer
2. Hallucination rate ≤ 20%?      → если нет, обязательно включать FORMATTER_JUDGE_CHECK
3. Strict JSON output reliable?    → проверить на benchmarks/dependency (deterministic)
4. Code-fine-tune?                 → +20% к pass-rate для Reviewer-роли
5. Context ≥ 64K?                  → нужно для repo-mode
6. Лицензия совместима?            → Apache 2.0 / MIT — best. GPL — проблема для дистрибуции
```

Эта рубрика отражена в наших калибровочных benchmark suites:
- `benchmarks/validator/cases.json` — измеряет accept/reject пригодность
- `benchmarks/dependency/cases.json` — измеряет deterministic JSON output (100% обязательно)
- `benchmarks/judge/cases.json` — измеряет judging пригодность (отчёты)
- `benchmarks/judge_formatter/cases.json` — измеряет judging пригодность (formatter gate)

**Любую новую модель прогоняйте через эти 4 suite перед заменой текущего default.**

---

## 8. Конкретный roadmap для ia-reviewer

### Сейчас (production)
- `AI_GATEWAY_URL` → Anthropic / Bifrost
- Все агенты → Sonnet 4.6
- `JUDGE_MODEL` пустой → judge тоже Sonnet 4.6 (subortimal — см. §5.3)

### Шаг 1 — улучшение без замены инфры
```bash
# В .env:
JUDGE_MODEL=claude-opus-4-6              # stronger judge ловит ошибки reviewer'а
REPORT_FORMATTER_MODEL=claude-opus-4-6   # для нагретой роли TL;DR
ENABLE_REPORT_FORMATTER=true             # включить полировку
FORMATTER_JUDGE_CHECK=true               # с inline-gate защитой
```
Стоимость: +20-30% LLM-бюджета, +5-10 sec latency. Качество: TL;DR + gate-проверка.

### Шаг 2 — частичный self-host (одна железка)
- Поднять **Qwen3.6-27B BF16** на 1× H100/H200 (~54 GB) или **FP8** на 1× L40S/RTX 6000 Ada (~27 GB)
- В `.env` указать `AI_GATEWAY_URL=http://<gpu-host>:8001/v1` для **только** reviewer-ролей
- `JUDGE_MODEL` оставить на proprietary через отдельный второй gateway (`Opus 4.6` или `GPT-5.3 Codex`)

Это даёт privacy для основного кода (reviewer'ы видят его файл-за-файлом), но оставляет judge на сильном proprietary. После Qwen3.6-27B этот шаг радикально дешевле прежнего: L40S за $1/hr вместо H200 за $3.5/hr.

### Шаг 3 — полный self-host (multi-GPU)
- **4× H200 (FP8)** → DeepSeek V3 — оптимальный production setup
- **8× H200 (FP8)** → Kimi K2.6 — для команд с самым высоким объёмом и privacy-требованиями
- Все роли на одном кластере
- Калибровка через `benchmarks/judge --min-success-rate 0.7` И `benchmarks/judge-formatter --min-success-rate 0.7` на новой модели

---

## 9. Что отвечать на защите

**В.: «А почему вы не используете local LLM, у вас же только Anthropic?»**

О.: «Architecture поддерживает любой OpenAI-compatible endpoint через `AI_GATEWAY_URL` (`src/models/factory.py::_resolve_gateway_model` — три уровня резолва). Текущий выбор Sonnet 4.6 — балланс cost/quality для ~1000 reviews/мес. В режиме self-host архитектура передаёт это решение оператору без изменения кода. Документ `docs/model-selection-research.md` фиксирует, что для одной GPU (24-141 GB) рекомендуем **Qwen3.6-27B** — это open Apache-2.0 модель с 77.2% SWE-bench Verified, в одном классе с Sonnet 4.6 (79.6%). Для multi-GPU — DeepSeek V3 или Kimi K2.6.»

**В.: «Что если model дала плохой результат — у вас есть защита?»**

О.: «Да, в трёх слоях:
1. **Деттерминистический baseline** — отчёт всегда генерируется без LLM-summary (`ReportRenderer`)
2. **Структурный JSON-парсер** — fail-soft на каждом LLM-вызове (`_parse_response`)
3. **`FORMATTER_JUDGE_CHECK`** — inline LLM-judge gate с 4 критериями: real_files, real_cves, severity_respect, no_invented_findings. Fail-CLOSED при ошибке судьи.

И мы измерили это: на gpt-oss-20b наш собственный self-review дал 12% signal-to-noise, что задокументировано в `docs/observed-quality-cases/`. Это и есть граница, ниже которой нельзя.»

**В.: «Какая модель сейчас лучшая для security code review?»**

О.: «По SWE-bench Verified — GPT-5.3 Codex (85%). По цене/качеству proprietary — Sonnet 4.6 (79.6%). По open-weights production — DeepSeek V3 (83.7%, требует 8× H200) или Kimi K2.6 (84%, тоже 8× H200). **На одной GPU (даже потребительского уровня) — Qwen3.6-27B (77.2%)** — единственная open модель, которая держит уровень frontier proprietary в Tier-S бюджете. Всё ниже 27B параметров без современной hybrid-attention архитектуры — повторение нашего gpt-oss-20b кейса, не использовать.»

---

## Sources

**Primary leaderboard — start here for any model question:**

- [Artificial Analysis — Models leaderboard](https://artificialanalysis.ai/leaderboards/models) — independent benchmarks across intelligence / latency / price for proprietary AND open-weight models

**Qwen3.6-27B (Tier-S default after April 2026 update):**

- [Qwen.ai — Qwen3.6-27B official blog](https://qwen.ai/blog?id=qwen3.6-27b) — official SWE-bench/HumanEval numbers + hybrid architecture
- [Hugging Face — Qwen/Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) — model card
- [BuildFastWithAI — Qwen3.6-27B Review](https://www.buildfastwithai.com/blogs/qwen3-6-27b-review-2026) — comparison with predecessor
- [LocalClaw — Qwen3.6-27B Deep Dive](https://localclaw.io/blog/qwen3-6-27b-deep-dive) — hybrid thinking, multimodal
- [vLLM Recipes — Qwen3.6-27B](https://recipes.vllm.ai/Qwen/Qwen3.6-27B) — deployment guide

**Kimi K2.6 (Tier-L frontier open):**

- [Artificial Analysis — Kimi K2.6 model page](https://artificialanalysis.ai/models/kimi-k2-6) — official specs (1000B total / 32B active)

**Open-weight landscape (rest):**

- [BenchLM.ai — Best Open Source LLM 2026](https://benchlm.ai/blog/posts/best-open-source-llm) — open-weights leaderboard
- [Spheron — GPU Requirements Cheat Sheet 2026](https://www.spheron.network/blog/gpu-requirements-cheat-sheet-2026/) — hardware → model fit
- [Spheron — DeepSeek V3.2 vs Llama 4 vs Qwen 3](https://www.spheron.network/blog/deepseek-vs-llama-4-vs-qwen3/) — code benchmarks, cost
- [MindStudio — GPT-5.4 vs Claude Opus 4.6 vs Gemini 3.1 Pro](https://www.mindstudio.ai/blog/gpt-54-vs-claude-opus-46-vs-gemini-31-pro-benchmarks) — proprietary frontier
- [AIMagicx — Claude Opus 4.6 vs GPT-5.4 vs Gemini 3.1 Pro April 2026](https://www.aimagicx.com/blog/claude-opus-4-6-vs-gpt-5-4-vs-gemini-3-1-benchmark-comparison-april-2026)
- [WhatLLM — Best LLM for Coding 2026](https://whatllm.org/best-llm-for-coding) — coding benchmarks
- [ModelsLab — LLM Hallucination Rates 2026](https://modelslab.com/blog/llm/llm-hallucination-rates-2026) — hallucination floor
- [BenchLM — GPT-OSS-120B benchmarks](https://benchlm.ai/models/gpt-oss-120b) — OpenAI's open release
- [DigitalApplied — Quantization Tradeoffs](https://www.digitalapplied.com/blog/quantization-tradeoffs-4bit-8bit-fp8-performance-data) — FP8/INT4 quality loss
- [Sesame Disk — Quantization Techniques 2026](https://sesamedisk.com/quantization-techniques-ai-inference-2026/) — GGUF/AWQ/GPTQ comparison
- [NVIDIA — H200 MLPerf Inference Records](https://developer.nvidia.com/blog/nvidia-h200-tensor-core-gpus-and-nvidia-tensorrt-llm-set-mlperf-llm-inference-records/) — H200 specs
- [llmhardware.io — Best Local LLM for Coding by GPU Tier](https://llmhardware.io/guides/best-llm-for-coding) — hardware budgeting
- [Pinggy — Best Open Source Self-Hosted LLMs for Coding 2026](https://pinggy.io/blog/best_open_source_self_hosted_llms_for_coding/)
- [BentoML — Best Open-Source LLMs in 2026](https://www.bentoml.com/blog/navigating-the-world-of-open-source-large-language-models)
- [Acecloud — Best Open Source LLMs in 2026: Deployment Guide](https://acecloud.ai/blog/best-open-source-llms/)
- [observed-quality-cases/gpt-oss-20b-2026-06-05.md](observed-quality-cases/gpt-oss-20b-2026-06-05.md) — наш собственный self-review кейс
