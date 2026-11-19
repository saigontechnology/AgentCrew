# Built-in model catalog refresh — 2026-11-19

This is a curated catalog update, not a complete mirror of vendor catalogs. It does
not add service implementations, mix endpoint families, blacklist raw model IDs,
or change user-defined provider/model configuration. Availability and pricing
were checked against the primary sources below on **2026-11-19**. No paid live
inference calls were made; account eligibility and quotas still apply.

## Defaults and migration

| Provider | New explicit default | Matching service fallback |
| --- | --- | --- |
| Anthropic | `claude-sonnet-5-5` | Same ID |
| Together | `deepseek-ai/DeepSeek-V4.1-Flash-1017` | Same ID |
| Fireworks | `accounts/fireworks/models/deepseek-v4p1-flash-1017` | Same ID |
| Codex (ChatGPT OAuth) | `gpt-6.1-sol` | Same ID |
| GitHub Copilot | `gpt-6.1-sol` | Same ID in existing `copilot_response` service family |
| OpenAI API | `gpt-6.2-sol` | Existing `openai_response` service family |

Copilot uses existing `gpt-6.1-sol`, as selected by the user. The previous
`gpt-5.6-terra` entry is retained pending resolution of conflicting lifecycle
observations, rather than treated as a confirmed retirement. Google retains
`gemini-3.6-flash`; OpenCode Go retains `kimi-k2.5`, with the existing `kimi-k2.6`
service fallback. Providers that previously relied on list ordering rather than
an explicit default keep that convention, except Codex and the user-approved
Sonnet 5.5 default. The final source reread also found retired constructor
fallbacks in Copilot Responses (`gpt-5.4`) and Anthropic (Claude 3.7); both are
aligned to the approved catalog defaults in this slice.

Catalog removal only removes a built-in picker/metadata entry. Explicit raw IDs,
private deployment IDs, and user-defined custom models are still accepted by
existing selection/service fallback paths. Persisted selections are not silently
rewritten: switch retired selections to their replacements explicitly.

## Metadata and compatibility decisions

- Effective contexts use the existing price-tier budget: at most 400,000 tokens
  for input prices <= $0.50/M, 256,000 for > $0.50 and < $3/M, and 128,000 for
  >= $3/M. Existing smaller limits and smaller endpoint limits are preserved.
  Existing oversized paid entries in the touched catalogs are reduced, not
  increased to the providers' million-token maxima. Subscription conventions
  and their practical limits are preserved.
- Subscription catalogs remain zero-priced. API prices are USD per million
  tokens and are estimates, not billing guarantees. Cache-write, long-context,
  region, priority, and time-of-day charges are not fully represented by the flat
  metadata schema.
- Anthropic Sonnet 5 is corrected to $2/$10 and Haiku 4.5 to $1/$5. Opus 5.5 is
  $4/$20. Haiku 5.5 uses the conservative **over-100K-prompt tier** ($0.50/$2.50,
  cache read $0.05), since its effective context can cross that threshold. Its
  <=100K tier is $0.10/$0.50, cache read $0.01.
- OpenAI GPT-5.6 Terra uses current non-promotional $2/$12 (cache $0.20) and Luna
  $0.20/$1.20 (cache $0.02). Sol retains its $5/$30 base estimate rather than the
  $4/$20 promotion ending **2026-11-30**. Its description identifies that policy.
  Existing GPT-5 and GPT-5.1 remain available until their scheduled shutdowns
  (2026-12-11 and 2027-04-01); their descriptions now mark them deprecated and
  neither is the default. GPT-5-Codex was also removed: the current API
  deprecation table explicitly lists its **2026-07-23** shutdown.
- Google base estimates deliberately exclude introductory discounts, as approved:
  Carbon/Argon $4/$20 (cache $0.20), Flash 3.8 $1.50/$7.50 (cache $0.15), Hydrogen
  $1.50/$7.50 (cache $0.075). Carbon/Argon's $2/$10 launch pricing ends
  **2026-11-30**; Flash 3.8's $0.75/$3.75 pricing ends **2026-12-31**. These
  conservative base values and context budgets have explicit regression checks.
- DeepInfra Kimi K3.1 Pro uses its published $2/$10 base rate, excluding the
  displayed 5% discount ($1.90/$9.50). Qwen4 Max uses the actual DeepInfra partner
  rates ($1.413/$4.24, cache $0.071), not another provider's prices.
- The four new native Claude IDs, including CommandCode's Fable 5.2 native route,
  use adaptive thinking without temperature/top-p/top-k or legacy token budgets.
  Existing/custom Claude IDs keep their previous request behavior. Legacy numeric
  thinking budgets map to low/medium/high effort only for the new IDs;
  explicit low/medium/high/xhigh/max effort is also supported; requesting
  zero selects low effort rather than sending a rejected thinking-disabled value.
  Auxiliary calls also avoid disabled thinking, and specification validation reads
  text after thinking blocks. No new endpoint family is involved.
- The new Gemini IDs support the existing GenerateContent path, including streaming
  and client-managed tool histories. The auxiliary path uses low `thinking_level`
  rather than the old `thinking_budget=0` setting for these IDs only. Existing and
  custom model request shaping is unchanged. No Interactions/long-decode migration
  is attempted.
- New Kimi K3.1 entries reuse the catalog's Kimi-family temperature-1 sampling
  convention. OpenCode's live API explicitly marks its K3.1 sampling controls
  unsupported; that separate addition is deferred rather than modifying the
  shared OpenAI-compatible contract.

### Bounded review corrections

Every auxiliary preference must resolve to a registered model, including aliases
whose preference key is a service family rather than the catalog provider.
Together now selects `together/MiniMaxAI/MiniMax-M3.1-Flash`; Codex selects
`openai_codex/gpt-6-luna` instead of its nonexistent `gpt-6.1-luna` spelling.

Onboarding uses the `_service_family` identity assigned by `ServiceManager`:
Copilot Responses and Codex select `gpt-6.1-sol`, while the GitHub chat-completions
family retains `claude-sonnet-4.6`. Provider identity alone no longer places a chat
model on the Copilot Responses service. Unrelated onboarding choices are unchanged.

## Lifecycle evidence and source conflicts

- Anthropic Opus 4.1 is retired; Sonnet 4.5 is deprecated and retires November 30.
- Together removals concern the **shared/serverless** endpoint that AsyncTogether
  calls. Availability for dedicated deployments does not justify retaining the
  same ID in this shared catalog.
- Fireworks' September 26 changelog confirms September 25 public-serverless
  removals, including Muse Glimmer 30B. Its October 19 notice removes the two
  Nemotron IDs from serverless on November 7. Old pricing rows still list those
  Nemotron models; the dated serverless lifecycle notice takes precedence. The
  November 2 DeepSeek training notice is not used as inference-retirement proof.
  Global MiniMax M3 and GLM 5.3 Flash are retained: only their **US** endpoints
  were affected. DeepSeek V4.1 Flash 1017 is already cataloged, not duplicated.
- The Fireworks public model cards on `fireworks.ai` confirm exact full IDs,
  Ready/serverless status, context, image/function support, and rates for Kimi
  K3.1 Pro, MiniMax M3.1 Flash, and Ember-1. Older `app.fireworks.ai` pages did not
  expose readable metadata; the primary public cards resolved that uncertainty.
- Copilot lifecycle observations conflict for `claude-opus-5` and `gpt-5.6-terra`.
  Architect's review of the canonical supported-model page found both listed as
  GA, with neither in its retirement history. An older/alternate indexed path
  showed November 16 retirements; a fresh correction-stage fetch of the canonical
  URL also returned those retirement rows. These public-document observations are
  unresolved, not evidence of consistent current availability or confirmed
  universal retirement. Under the conservative removal policy and explicit
  review decision, both pre-refresh definitions and service families are retained.
  Account, plan, and client availability still require confirmation. Opus 4.8 and
  Sonnet 4.6 remain retained because documented annual-plan exceptions mean their
  retirement is not universal.
- No DeepInfra models are removed merely because newer models supersede them.
  xAI and Refuseless are unchanged.

## Deferred entries

| Provider | Deferred | Reason |
| --- | --- | --- |
| Copilot | Fable 5.1/5.2; Gemini 3.8 Flash and 4 Argon/Carbon/Hydrogen; MAI-Code-1.1-Flash; Grok 4.5/4.6/4.7; Kimi K3 | Current supported-model documentation names products, but did not provide unambiguous exact IDs plus chat-completions/Responses routing for AgentCrew's API. Names are not converted into guessed slugs. Plan/client availability also varies. |
| Copilot | GPT-6.2 entries | Supported-model and API-family evidence was not jointly unambiguous. |
| OpenCode Go | Kimi K3.1 Pro | Listed, but API metadata disables temperature/top-p while the existing service sends sampling parameters. Defer the compatibility work rather than broaden this slice. |
| OpenCode Go | Mistral Large 4, Haiku 5.5 | Not established as callable IDs on the existing Go endpoint by the fetched live catalog. Do not transplant Zen/other-plan IDs. |
| OpenCode Go | GPT-6 Luna, Grok 4.7, Gemini 4 Argon, Muse Responses variants | Responses/Google or mixed-family routing is outside scope. |
| CommandCode | GPT-6.2 Astra/Sol; Gemini 4 Carbon/Argon/Neon; Grok 4.7 | IDs/names appear in the provider catalog, but per-model compatibility with the existing AgentCrew service route was not sufficiently confirmed. No new Responses/Google routing is added. |
| CommandCode | DeepSeek V4.1 Flash | Several latest/fast/ultrafast/time-of-day variants exist; avoid selecting an ambiguous moving alias or encoding promotion-dependent metadata in this curated slice. |
| General | Account, plan, eligibility, region, quota, or gateway-specific behavior | Public documentation is not an authenticated entitlement or paid inference test. Fable 5.2 availability can be restricted by Anthropic eligibility. |

## Primary sources checked

- Anthropic: [models](https://platform.claude.com/docs/en/about-claude/models/overview),
  [pricing](https://platform.claude.com/docs/en/about-claude/pricing),
  [deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations),
  [thinking and sampling rules](https://platform.claude.com/docs/en/build-with-claude/thinking),
  [supported effort levels](https://platform.claude.com/docs/en/build-with-claude/effort).
- OpenAI: [API models](https://developers.openai.com/api/docs/models),
  [pricing](https://developers.openai.com/api/docs/pricing),
  [API deprecations](https://developers.openai.com/api/docs/deprecations),
  [Codex models and ChatGPT retirement notices](https://developers.openai.com/codex/models).
- Google: [model catalog](https://ai.google.dev/gemini-api/docs/models),
  [pricing](https://ai.google.dev/gemini-api/docs/pricing),
  [Carbon GenerateContent/long-decode compatibility](https://ai.google.dev/gemini-api/docs/whats-new-gemini-4-carbon),
  [Argon](https://ai.google.dev/gemini-api/docs/whats-new-gemini-4-argon),
  [Hydrogen](https://ai.google.dev/gemini-api/docs/whats-new-gemini-4-hydrogen),
  [Flash 3.8](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8).
- GitHub: [supported models and retirement history](https://docs.github.com/en/copilot/reference/ai-models/supported-models).
- Together: [serverless catalog](https://docs.together.ai/docs/serverless-models),
  [deprecations](https://docs.together.ai/docs/deprecations),
  [reasoning](https://docs.together.ai/docs/reasoning-overview).
- Fireworks: [changelog](https://docs.fireworks.ai/updates/changelog),
  [serverless pricing](https://docs.fireworks.ai/serverless/pricing),
  [recommended models](https://docs.fireworks.ai/guides/recommended-models),
  [Kimi K3.1 Pro](https://fireworks.ai/models/fireworks/kimi-k3p1-pro),
  [MiniMax M3.1 Flash](https://fireworks.ai/models/fireworks/minimax-m3p1-flash),
  [Ember-1](https://fireworks.ai/models/fireworks/ember-1).
- DeepInfra: [Kimi K3.1 Pro](https://deepinfra.com/moonshotai/Kimi-K3.1-Pro/api),
  [Qwen4 Max](https://deepinfra.com/Qwen/Qwen4-Max/api),
  [Nemotron 3.5 Super VL](https://deepinfra.com/nvidia/NVIDIA-Nemotron-3.5-Super-VL/api).
- OpenCode: [Go](https://opencode.ai/docs/go),
  [live Go model metadata](https://opencode.ai/zen/go/v1/models).
- CommandCode: [exact model reference](https://commandcode.ai/docs/reference/cli/models),
  [pricing/model profiles](https://commandcode.ai/models).

## Validation

No-network checks live under `verification/llm/`, not the deprecated `tests/`
folder. They cover catalog uniqueness and provider identity, defaults, retired
entries, known service families, fallback/default agreement, effective contexts,
subscription pricing, registry selection, raw/custom fallback preservation, and
Claude/Google request parameters. Review coverage also verifies every auxiliary
preference (including service-family aliases), onboarding endpoint-family safety,
and the retained Copilot definitions. Ruff and syntax/import checks apply to all
changed Python files. Full application integration and paid-provider inference
are not claimed.

## Exact catalog changes

**36 additions; 38 removals.** Already-cataloged models promoted to defaults are not counted as additions.

### claude

Added:

- `claude-fable-5-2`
- `claude-haiku-5-5`
- `claude-opus-5-5`
- `claude-sonnet-5-5`

Removed:

- `claude-opus-4-1`
- `claude-sonnet-4-5`


### openai

Added:

- `gpt-6-luna`
- `gpt-6.1-terra`
- `gpt-6.2-astra`
- `gpt-6.2-sol`

Removed:

- `gpt-5-codex`
- `gpt-5.1-codex`
- `gpt-5.1-codex-max`
- `gpt-5.1-codex-mini`


### openai_codex

Removed:

- `gpt-5.3-codex-spark`
- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.5`


### google

Added:

- `gemini-3.8-flash`
- `gemini-4-argon`
- `gemini-4-carbon`
- `gemini-4-hydrogen`

Removed:

- `gemini-pro-latest`


### deepinfra

Added:

- `Qwen/Qwen4-Max`
- `moonshotai/Kimi-K3.1-Pro`
- `nvidia/NVIDIA-Nemotron-3.5-Super-VL`


### together

Added:

- `MiniMaxAI/MiniMax-M3.1-Flash`
- `Qwen/Qwen4-Max`
- `moonshotai/Kimi-K3.1-Pro`
- `nvidia/NVIDIA-Nemotron-3.5-Super-VL`
- `zai-org/GLM-5.3`
- `zai-org/GLM-5.3-Flash`

Removed:

- `MiniMaxAI/MiniMax-M3`
- `Qwen/Qwen3.8-2.4T-A95B`
- `deepseek-ai/DeepSeek-V4-Flash-0731`
- `deepseek-ai/DeepSeek-V4-Pro`
- `google/gemma-4-31B-it`
- `moonshotai/Kimi-K2.6`
- `moonshotai/Kimi-K2.7-Code`
- `nvidia/nemotron-3-ultra-550b-a55b`
- `thinkingmachines/Inkling`


### opencode_go

Added:

- `deepseek-v4.1-flash`
- `hy4`
- `minimax-m3.1-flash`
- `step-5-preview`


### fireworks

Added:

- `accounts/fireworks/models/ember-1`
- `accounts/fireworks/models/kimi-k3p1-pro`
- `accounts/fireworks/models/minimax-m3p1-flash`

Removed:

- `accounts/fireworks/models/deepseek-v4-flash-0731`
- `accounts/fireworks/models/deepseek-v4-flash-vision-exp`
- `accounts/fireworks/models/deepseek-v4-pro`
- `accounts/fireworks/models/deepseek-v4-pro-0813`
- `accounts/fireworks/models/glm-5p2`
- `accounts/fireworks/models/kimi-k2p6`
- `accounts/fireworks/models/kimi-k2p7-code`
- `accounts/fireworks/models/minimax-m2p7`
- `accounts/fireworks/models/muse-glimmer-30b`
- `accounts/fireworks/models/nemotron-3-ultra-nvfp4`
- `accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b`


### github_copilot

Retained after review, not counted as additions:

- `claude-opus-5`
- `gpt-5.6-terra`

Removed:

- `gemini-2.5-pro`
- `gemini-3-flash`
- `gemini-3.1-pro`
- `gemini-3.5-flash`
- `kimi-k2.7-code`
- `mai-code-1-flash`
- `raptor-mini-tertiary`


### commandcode

Added:

- `Qwen/Qwen4-Max`
- `claude-fable-5-2`
- `inclusionai/ling-3.1-flash-1019`
- `minimax/minimax-m3.1-flash`
- `mistral/mistral-large-4`
- `moonshotai/Kimi-K3.1-Pro`
- `nvidia/nemotron-3.5-super-vl`
- `tencent/hy4`
