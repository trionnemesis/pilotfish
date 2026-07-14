# routing-spec 技術規格

**狀態：** Draft v0.1  
**定位：** 以 pilotfish 的三層、角色式編排為基礎，加入可驗證的 Task Envelope、確定性路由、執行 ledger、best-effort model attestation、eval harness，以及 Claude／Codex adapter。  
**規範用語：** `MUST`、`MUST NOT`、`SHOULD`、`MAY` 分別表示必須、禁止、建議、可選。

---

## 1. 基準與前提

以下 repo 現況視為本規格的輸入前提，不由本文件重新查證：

- `main` 目前只有 2 commits。
- 目前為 9 stars。
- GitHub Releases 頁沒有 published releases。
- 前文提及的 `v1.1.5` 不得只依 Releases UI 判斷是否存在；fork 前 MUST 在本地執行 `git fetch --tags` 與 `git tag -l` 確認實際 tags。
- 現行設計為三層結構與六個 subagent roles。

目標版本新增 `senior-executor` 後，subagent role 數量將由六個變成七個；另有一個不落地為 subagent 檔案的虛擬 `orchestrator` control-plane role。

---

## 2. 問題定義

現有 policy-driven orchestration 可讓主模型依自然語言規則選擇 subagent，但缺少以下可機器驗證的控制面：

1. 任務分類結果沒有標準結構。
2. `envelope → role` 的映射無法以純函式測試。
3. 無 append-only runtime delegation ledger。
4. 無法區分「設定宣稱使用某模型」與「執行時觀測到某模型」。
5. 自然語言分類與確定性路由混在同一層，無法分別評估。
6. Claude 與 Codex 的能力不對稱，缺少 adapter capability report 與降級語意。

`routing-spec` 將主模型保留為規劃、判斷、拆解、例外處理與最終審查者；大量執行工作由較低成本或較適合的角色處理。

---

## 3. 目標與非目標

### 3.1 目標

- 定義 vendor-neutral 的 canonical routing spec。
- 將自然語言分類與確定性路由分離。
- 讓 `route()` 成為可在 CI 執行的純函式。
- 以角色名稱隔離 policy 與模型名稱。
- 建立單調、不可降級的 escalation。
- 保持執行者與驗證者分離。
- 記錄每次委派、模型聲明、模型觀測、成本與結果。
- 提供 Claude adapter 與 Codex adapter。
- 提供安全、可回滾、需人類批准的安裝流程。
- 提供 L1 deterministic eval 與 L2 stochastic eval。

### 3.2 非目標

- 不宣稱提供密碼學等級的模型身分證明。
- 不宣稱自然語言 `classify()` 可完全 deterministic。
- 不取代 Claude Code、Codex 或模型供應商本身的 runtime。
- 不允許 subagent 形成任意遞迴 agent tree。
- 不以關閉 sandbox、approval 或 prompt-injection 防護換取安裝便利。
- v0.1 不要求 Claude 與 Codex 具備完全相同的功能。

---

## 4. 本 Draft 採用的設計決策

### D1. `migration` 不加入 `task_type`

`task_type` 描述工作型態；migration 描述跨領域風險。相同 migration 任務可同時是 mechanical、judgment 或 verification，因此 v0.1 保留五個 closed enum，新增可擴充的 `risk_tags`。

規範：

- `task_type` 保持 `{recon, mechanical, judgment, security, verification}`。
- `risk_tags` MAY 包含 `migration`。
- `risk_tags` 含 `migration` 時，router MUST 將有效風險下限提升為 `high`，除非上游以可稽核的明確 policy exception 覆寫；v0.1 reference implementation 不提供此 exception。

### D2. 採用 deterministic pre-classifier，再以 LLM 補齊殘餘案例

- 高信心、可明確證明的規則先處理。
- 規則衝突、無匹配或資訊不足時，才交給 in-band LLM classifier。
- pre-classifier MUST fail open to LLM，不得用低信心 keyword match 強行分類。

### D3. 保留 security pre-route，但縮小其主張

- 高信心 security signal MUST 由 deterministic pre-classifier 直接產生 `task_type=security`。
- 一旦任務被分類為 security，主 session MUST NOT 親自執行其修改工作。
- security 任務 MUST 路由到 `security-executor`。
- 主 session MAY 規劃、補規格與審查，但不得把 security 實作降級到 Sonnet roles。
- 無法由 deterministic 規則確認的模糊案例仍 MAY 經 LLM classifier；本規格不宣稱完全避開主模型分類。

### D4. Eval 先 L1、後 L2

- L1 MUST 為 blocking CI gate。
- L2 初期 MUST 為非 blocking、需 credits、可重複執行的報告。
- 在建立穩定 baseline 前，不設定硬編碼的 L2 pass-rate release gate。

---

## 5. 系統架構

```text
User Prompt
    │
    ▼
Deterministic Pre-classifier ── hard match ──┐
    │ no match / conflict                    │
    ▼                                        │
In-band LLM Classifier                       │
    │                                        │
    └──────────────► Task Envelope ◄─────────┘
                           │
                           ▼
                Deterministic Router
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
         Delegate        Refine        Takeover/Block
             │
             ▼
        Role Subagent
             │
        Attest + Ledger
             │
             ▼
      Independent Verifier
             │
      CONFIRMED / REFUTED
             │
             ▼
        Orchestrator Review
```

核心元件：

1. `preclassifier`
2. `classifier`
3. `router`
4. `role_registry`
5. `delegator`
6. `attestor`
7. `ledger`
8. `verifier`
9. `compiler`
10. `eval_runner`
11. `installer`

---

## 6. Canonical State Model

### S1. Task Envelope

Task Envelope 是路由用的結構化狀態，不等同完整執行 prompt。

```yaml
schema_version: "0.1"
task_id: "stable-unique-id"
parent_task_id: null

task_type: recon              # closed enum
spec_completeness: partial     # closed enum
risk_level: low                # closed enum
risk_tags: []                  # extensible strings; built-in: migration
failure_count: 0               # integer >= 0, monotonic per task_id

classification_source: rule    # manual | rule | llm
classification_evidence: "rule:read_only_lookup"
```

#### S1 invariants

- `task_type` MUST 為：
  - `recon`
  - `mechanical`
  - `judgment`
  - `security`
  - `verification`
- `spec_completeness` MUST 為：
  - `fully_specified`
  - `partial`
  - `ambiguous`
- `risk_level` MUST 為：
  - `low`
  - `medium`
  - `high`
- `failure_count` MUST 為非負整數。
- 同一 `task_id` 的 `failure_count` MUST 單調不減。
- 重新補規格後若要重新開始 failure budget，MUST 建立新的 `task_id`，並以 `parent_task_id` 指向原任務；不得重設原任務的 `failure_count`。
- `risk_tags` 含 `migration` 時，effective risk MUST 為 `high`。

### S1b. Delegation Spec

`delegate(role, spec)` 使用獨立的 Delegation Spec，避免把所有原始 prompt 與程式內容寫入 routing envelope。

```yaml
objective: "要達成的具體結果"
constraints:
  - "不可改 public API"
done_criteria:
  - "相關測試通過"
allowed_paths:
  - "src/auth/**"
forbidden_paths:
  - "migrations/**"
context_refs:
  - "issue:123"
```

規範：

- `mechanical` 任務的 Delegation Spec MUST 含明確 `objective`、`constraints`、`done_criteria` 與 scope。
- 若不符合，`spec_completeness` 不得為 `fully_specified`。
- Ledger SHOULD 儲存 spec hash 或引用，不應預設保存完整原始 prompt、secret 或完整程式內容。

### S2. Role Registry

```yaml
roles:
  executor:
    model_alias: sonnet
    effort: high
    allowed_tools: []
    disallowed_tools: [Agent, Workflow]
    can_spawn: false
```

每個 role entry MUST 包含：

- `model_alias`
- `effort`
- `allowed_tools`
- `disallowed_tools`
- `can_spawn`

`model_alias` MUST 是 logical alias，不應在 policy prose 中出現完整 model ID。

#### v0.1 role registry

| Role | 類型 | Model alias | Effort | Spawn | 主要用途 |
|---|---|---:|---:|---:|---|
| `orchestrator` | virtual control-plane | `best` | high | Yes | 規劃、分類、拆解、refine、最終審查 |
| `scout` | leaf | `haiku` | low | No | 一般 read-only reconnaissance |
| `Explore` | leaf | `haiku` | low | No | 覆寫／承接 exploration 類工作 |
| `mech-executor` | leaf | `sonnet` | low | No | 低風險、完整規格的機械工作 |
| `executor` | leaf | `sonnet` | high | No | 一般 judgment implementation |
| `senior-executor` | leaf | `opus` | high | No | 高風險或升級後的 implementation |
| `verifier` | leaf | `opus` | medium | No | fresh-context adversarial verification |
| `security-executor` | leaf | `opus` | high | No | security-sensitive execution |

Tool constraints：

- `scout`、`Explore` MUST 使用 positive allowlist，至少只允許 read/search 類工具。
- `mech-executor`、`executor`、`senior-executor`、`security-executor` MUST 禁止 `Agent` 與 `Workflow`。
- `verifier` MUST 禁止 `Write`、`Edit`、`NotebookEdit`、`Agent`、`Workflow`。
- 所有 leaf roles 的 `can_spawn` MUST 為 `false`。

### S3. Runtime Delegation Record

原始單一 `verdict` 欄位不足以同時表示執行結果、驗證結果與 attestation 狀態。v0.1 將三者拆開：

```yaml
record_id: "append-only-record-id"
task_id: "task-id"
sequence: 3
timestamp: "RFC3339"

envelope_snapshot: { ... }
role_invoked: executor
model_claimed: sonnet
model_attested: null
attestation_method: transcript
attestation_status: UNKNOWN

token_usage:
  input: null
  output: null
  total: null
latency_ms: null

execution_status: SUCCEEDED
verifier_verdict: null
escalated_from: mech-executor
supersedes_record_id: null
```

Closed enums：

- `execution_status`：`SUCCEEDED | FAILED | BLOCKED | CANCELLED | INVALIDATED`
- `attestation_status`：`MATCHED | MISMATCHED | UNKNOWN | NOT_APPLICABLE`
- `verifier_verdict`：`CONFIRMED | REFUTED | null`

Ledger invariants：

- Ledger MUST append-only。
- 既有 record MUST NOT update 或 delete。
- 更正資料 MUST 追加新 record，並以 `supersedes_record_id` 指向被更正 record。
- 不可取得 token、latency 或 model observation 時，欄位 MUST 為 `null`，不得猜測。
- `MISMATCHED` run MUST 標記為 `INVALIDATED`，不得作為已驗證成果接受。
- `UNKNOWN` 表示證據不足，不得被描述成已證明使用指定模型。

### S4. Verifier Verdict

```text
CONFIRMED | REFUTED
```

- MUST 為 closed enum，無第三態。
- 若 verifier 本身因工具、runtime 或輸入缺失而無法完成，這是 `execution_status=FAILED|BLOCKED`，不是第三種 verdict。
- `REFUTED` 表示 verifier 成功完成工作並找到可重現反例。
- verifier MUST NOT 修復其發現。

### S5. Config Layer State

沿用三層結構：

| Layer | Canonical responsibility |
|---|---|
| Machine | 主模型、fallback、runtime-level settings |
| Roles | role → model／effort／tool capability binding |
| Policy | 如何分類、委派、升級與驗證；只引用 role name |

規範：

- Policy MUST NOT 直接綁定 model name。
- 每個 named role 的 model binding MUST 只有一個 source of truth。
- 呼叫 named role 時 MUST NOT 以 invocation-level model 覆蓋 role binding。
- 只有真正 ad-hoc、沒有 registry entry 的 agent MAY 指定 invocation-level model。

### S6. Eval Fixtures

L1 fixture：

```yaml
id: mechanical-low-risk
input:
  task_type: mechanical
  spec_completeness: fully_specified
  risk_level: low
  risk_tags: []
  failure_count: 0
history: []
expected:
  action: DELEGATE
  role: mech-executor
  forbidden_roles: [senior-executor, security-executor, verifier]
```

L2 fixture：

```yaml
id: natural-language-auth-fix
task_description: "修正 refresh token 重放漏洞並補測試"
accepted_envelopes:
  task_type: [security]
  risk_level: [high]
expected_route:
  action: DELEGATE
  role: security-executor
runs: configurable
```

---

## 7. Action Contracts

### A1. `preclassify(context) -> PartialEnvelope | NO_MATCH | CONFLICT`

- MUST deterministic。
- MUST 只輸出規則能證明的欄位。
- MUST 記錄 `rule_id` 作為 evidence。
- `NO_MATCH` 或 `CONFLICT` MUST 交給 `classify()`。
- 不得以單一低信心 keyword 作為 security 或 migration 的唯一依據。

初始規則 SHOULD 優先處理：

- 明確 read-only lookup。
- 明確 verification-only request。
- 明確 security-sensitive domain/action。
- 結構化 metadata 中的 `migration` risk tag。

### A2. `classify(prompt, partial_envelope?) -> TaskEnvelope`

- 目前為 in-band LLM classification。
- 輸出 MUST 通過 schema validation。
- schema invalid、enum 越界或欄位矛盾時，MUST 回到 orchestrator refine，不得猜值補齊。
- classification 本身不能以 model attestation 證明為 deterministic；其品質由 L2 eval 衡量。

### A3. `route(envelope, history) -> RoutingDecision`

因為 ambiguous、blocked 與 takeover 並不一定有 subagent role，正式介面不採用單純 `role` 回傳值：

```yaml
action: DELEGATE       # DELEGATE | REFINE | TAKEOVER | BLOCK
role: executor         # 僅 DELEGATE 時必填
reason_code: judgment_default
```

`route()` MUST 是無 side effect 的純函式；相同 envelope、registry 與 history MUST 產生相同結果。

### A4. `delegate(role, delegation_spec) -> RunHandle`

- MUST 使用 registry 的 role definition。
- MUST NOT 對 named role 指定不同 model。
- MUST 將 `task_id`、done criteria 與 scope 傳入 subagent。
- writing agents 並行時 SHOULD 使用隔離 workspace／worktree；若 target runtime 不支援，adapter MUST 顯示 degradation warning。

### A5. `verify(work_claim) -> CONFIRMED | REFUTED`

- MUST 使用獨立 fresh context。
- MUST 重做關鍵測試或操作，不得只接受 executor 自述。
- MUST 僅回報 evidence 與 verdict，不得寫入修復。
- security work 的 verifier prompt MUST 切換為 exhaustive profile，但不改變 `verifier` 的 model binding source。

### A6. `escalate(envelope, history) -> RoutingDecision`

- MUST 單調上升。
- MUST NOT 從 Opus tier 降回 Sonnet tier。
- MUST 依 task-specific ladder 決定，而不是把所有 role 排成單一權限階梯。
- reaching orchestrator 表示 refine、takeover 或建立 child task；不得重設原 task 的 failure count。

### A7. `attest(run) -> Attestation`

- MUST 區分 configured evidence 與 runtime-observed evidence。
- MUST 偵測 `CLAUDE_CODE_SUBAGENT_MODEL` 是否存在。
- 若該環境變數覆蓋 role frontmatter，MUST 記錄 `MISMATCHED` 或明確 override 狀態，且不得靜默接受。
- hooks、SubagentStop、transcript parsing 或 provider logs 僅屬 best-effort observation。
- 沒有 observation 時 MUST 回傳 `UNKNOWN`，不得把 `model_claimed` 複製成 `model_attested`。

### A8. `compile(spec, target) -> AdapterArtifacts + CapabilityReport`

- Canonical spec MUST 與 target-specific artifacts 分離。
- Compiler MUST 產生 deterministic output；相同 input 產物應 byte-stable，排除明確 timestamp 類 metadata。
- 不支援的能力 MUST 出現在 capability report，不得無聲降級。
- strict mode 下，required capability 缺失 MUST compile failure。

### A9. `eval_run(fixtures) -> EvalReport`

- L1 MUST 可離線、無 LLM、無 credits 執行。
- L2 MUST 支援多次 run、保存每次 envelope、彙總 pass rate 與 variance。
- L2 不得只回傳單一 pass/fail 結論。

---

## 8. Deterministic Routing Rules

### 8.1 Precedence

Router MUST 依下列順序處理：

1. Schema／invariant validation。
2. `risk_tags` 導出的 effective risk。
3. Security lane。
4. Verification lane。
5. Spec completeness gate。
6. Base role selection。
7. Risk floor。
8. Escalation floor from history／failure count。
9. No-downgrade invariant。

### 8.2 Spec completeness gate

| 條件 | Decision |
|---|---|
| `ambiguous` 且非 security | `REFINE` |
| `partial` 且 task_type 為 mechanical／judgment／verification | `REFINE` |
| `partial` 且 task_type 為 recon | 可 `DELEGATE scout` |
| security | 固定進 security lane；若規格不足，由 security-executor 回報 `BLOCKED`，不得猜測實作 |

### 8.3 Base routing

| Task type | Effective risk | Base decision |
|---|---|---|
| recon | any | `scout` |
| mechanical | low | `mech-executor` |
| mechanical | medium | `executor` |
| mechanical | high | `senior-executor` |
| judgment | low／medium | `executor` |
| judgment | high | `senior-executor` |
| security | any | `security-executor` |
| verification | any | `verifier` |

### 8.4 Escalation ladders

#### Recon

| failure_count | Decision |
|---:|---|
| 0–1 | `scout` |
| ≥2 | `REFINE`／orchestrator takeover |

Recon 不可為了升級模型而改用具有寫入能力的 executor。

#### Mechanical, low risk

| failure_count | Decision |
|---:|---|
| 0–1 | `mech-executor` |
| 2–3 | `executor` |
| 4–5 | `senior-executor` |
| ≥6 | `REFINE`／orchestrator takeover |

#### Mechanical, medium risk；Judgment, low／medium risk

| failure_count | Decision |
|---:|---|
| 0–1 | `executor` |
| 2–3 | `senior-executor` |
| ≥4 | `REFINE`／orchestrator takeover |

#### Mechanical／Judgment, high risk

| failure_count | Decision |
|---:|---|
| 0–1 | `senior-executor` |
| ≥2 | `REFINE`／orchestrator takeover |

#### Security

| failure_count | Decision |
|---:|---|
| 0–1 | `security-executor` |
| ≥2 | `REFINE`／orchestrator takeover；後續執行仍不得降級到 Sonnet |

#### Verification

- `REFUTED` 是 verifier 成功，不是 verifier failure。
- `REFUTED` MUST 將被驗證的 execution attempt 視為未通過，並增加該 parent task 的 failure count。
- verifier 自身 runtime/tool failure 連續達 2 次時，MUST 回 orchestrator 處理；不得建立會修復程式的 verifier。

### 8.5 No-downgrade

Router MUST 讀取同一 `task_id` 的 history，計算已使用過的最高 execution tier。新 decision 不得低於該 tier。

例：

```text
mech-executor → executor → senior-executor
```

允許：

```text
senior-executor → REFINE → 新 child task → senior-executor
```

禁止：

```text
senior-executor → executor
security-executor → executor
```

---

## 9. Failure Semantics

以下事件增加 task failure count：

- executor 回傳 `FAILED`。
- executor 回傳因 misroute／spec contradiction 造成的 `BLOCKED`。
- verifier 對該 execution attempt 回傳 `REFUTED`。

以下事件不直接增加 task failure count：

- attestation `MISMATCHED`：此 run 為 `INVALIDATED`，屬 infrastructure/config error。
- 使用者取消：`CANCELLED`。
- verifier 自身 runtime failure：記在 verifier run，不等同被驗證工作失敗。

任何失敗計數更新 MUST 以追加 ledger record 表示。

---

## 10. Attestation Model

Attestation 證據分級：

| Level | 證據 | 可宣稱內容 |
|---|---|---|
| Configured | role registry／frontmatter | 「設定要求使用 X」 |
| Observed | hook／transcript／provider metadata | 「runtime 觀測到 X」 |
| Unknown | runtime 無可用 metadata | 「無法確認實際模型」 |

v0.1 明確不提供 cryptographic attestation。

最少檢查：

1. role frontmatter model alias。
2. invocation 是否覆蓋 named-role model。
3. `CLAUDE_CODE_SUBAGENT_MODEL` 是否設置。
4. 可用時解析 SubagentStop／transcript metadata。
5. `model_claimed != model_attested` 時標記 `MISMATCHED`。

安裝器不得自行 unset 環境變數；MUST 顯示影響並要求明確批准或由使用者自行處理。

---

## 11. Adapter Specification

### 11.1 Canonical capability vocabulary

每個 adapter MUST 回報：

```yaml
capabilities:
  per_role_model_binding: supported | degraded | unsupported
  per_role_tool_policy: supported | degraded | unsupported
  child_spawn_control: supported | degraded | unsupported
  fresh_context_verifier: supported | degraded | unsupported
  runtime_model_observation: supported | degraded | unsupported
  isolated_parallel_writes: supported | degraded | unsupported
```

### 11.2 Claude adapter

Claude adapter MUST 編譯為：

1. Machine settings patch。
2. 七個 role agent definitions。
3. Role-only orchestration policy block。
4. Optional attestation hooks／transcript parser configuration。
5. Capability report。

Claude-specific invariants：

- named role invocation MUST omit model override。
- `CLAUDE_CODE_SUBAGENT_MODEL` MUST 在 preflight 與 runtime 被檢查。
- `scout`／`Explore` 使用 positive tool allowlist。
- role name collision MUST 由 frontmatter name 判斷，不只看 filename。

### 11.3 Codex adapter

Codex target MUST 以 capability discovery 為前提，不假設其與 Claude 的 agents frontmatter 同構。

- 若 target 無 per-role model binding，compiler MAY 產生 prompt-level policy，但 MUST 標記為 `degraded`。
- 若 target 無 runtime model observation，attestation MUST 為 `UNKNOWN`，不得偽裝成 `MATCHED`。
- strict compile mode 下，使用者要求的 required capability 缺失 MUST fail。
- Codex adapter 的實際檔案布局與可用控制項，必須在 implementation phase 以當時 CLI 實際能力確認；本規格不補造未確認機制。

---

## 12. Eval Specification

### 12.1 L1 — Deterministic routing eval

L1 input 是已結構化 envelope，不呼叫 LLM。

MUST 測試：

- 五種 task types。
- 三種 spec completeness。
- 三種 risk levels。
- `migration` risk tag 提升到 high。
- security pre-route。
- verifier isolation。
- 每條 escalation ladder 邊界。
- no-downgrade。
- invalid enum／negative failure count。
- named-role model ownership。
- all leaf roles `can_spawn=false`。
- verifier 無 write tools。

L1 MUST 在 CI 全數通過。

#### Fixture examples

```yaml
- id: recon-default
  envelope:
    task_type: recon
    spec_completeness: partial
    risk_level: low
    risk_tags: []
    failure_count: 0
  expected: {action: DELEGATE, role: scout}

- id: mechanical-low
  envelope:
    task_type: mechanical
    spec_completeness: fully_specified
    risk_level: low
    risk_tags: []
    failure_count: 0
  expected: {action: DELEGATE, role: mech-executor}

- id: mechanical-second-tier
  envelope:
    task_type: mechanical
    spec_completeness: fully_specified
    risk_level: low
    risk_tags: []
    failure_count: 2
  expected: {action: DELEGATE, role: executor}

- id: migration-forces-senior
  envelope:
    task_type: judgment
    spec_completeness: fully_specified
    risk_level: low
    risk_tags: [migration]
    failure_count: 0
  expected: {action: DELEGATE, role: senior-executor}

- id: security-fixed-lane
  envelope:
    task_type: security
    spec_completeness: partial
    risk_level: medium
    risk_tags: []
    failure_count: 0
  expected: {action: DELEGATE, role: security-executor}

- id: verification-only
  envelope:
    task_type: verification
    spec_completeness: fully_specified
    risk_level: high
    risk_tags: []
    failure_count: 0
  expected: {action: DELEGATE, role: verifier}

- id: ambiguous-judgment
  envelope:
    task_type: judgment
    spec_completeness: ambiguous
    risk_level: medium
    risk_tags: []
    failure_count: 0
  expected: {action: REFINE, role: null}
```

### 12.2 L2 — Stochastic classification eval

L2 input 是自然語言任務，透過 headless agent 執行多次分類。

每次 run MUST 保存：

- 原始 fixture id。
- classifier source。
- 產出的 envelope。
- schema validity。
- envelope 是否落在 accepted range。
- deterministic router output。
- expected route match。
- latency／tokens，若 runtime 可提供。

報告 MUST 至少包含：

- Envelope schema-valid rate。
- Field-level agreement。
- Final route agreement。
- Security recall。
- Per-fixture variance。
- Model／CLI version metadata，若可取得。

L2 在 v0.1 為非 blocking。`runs`、credits budget 與 release threshold 為 implementation configuration，不在本規格硬編數值。

---

## 13. Installation and Update Specification

Installer MUST：

1. 先執行 read-only preflight。
2. 顯示 exact change plan。
3. 取得人類明確批准後才寫入。
4. merge 指定 keys，不重寫整份 user config。
5. 對所有被修改檔案建立 backup。
6. 偵測 role name collision。
7. 偵測 `CLAUDE_CODE_SUBAGENT_MODEL`。
8. 支援 dry-run。
9. 支援 idempotent update。
10. 產生 rollback manifest。
11. 提供 uninstall，且只移除 routing-spec 自己擁有或仍與 template 相同的內容。
12. 不收集或寫入 API keys。
13. 不要求繞過 WebFetch／prompt-injection 保護。

Fork／安裝前 tag 檢查：

```sh
git fetch --tags --force
git tag -l
git show-ref --tags
```

Releases UI 不得作為 tag 是否存在的唯一證據。

---

## 14. Suggested Repository Layout

```text
routing-spec/
├── README.md
├── SPEC.md
├── routing.yaml
├── schemas/
│   ├── task-envelope.schema.json
│   ├── role-registry.schema.json
│   ├── ledger-record.schema.json
│   └── eval-fixture.schema.json
├── router/
│   ├── preclassifier.*
│   ├── classifier-contract.*
│   ├── route.*
│   ├── escalation.*
│   └── invariants.*
├── adapters/
│   ├── claude/
│   │   ├── templates/
│   │   ├── compiler.*
│   │   └── attestor.*
│   └── codex/
│       ├── compiler.*
│       ├── capability_probe.*
│       └── attestor.*
├── evals/
│   ├── l1-routing.yaml
│   ├── l2-classification.yaml
│   └── runner.*
├── install/
│   ├── AGENT-INSTALL.md
│   ├── dry-run.*
│   └── uninstall.*
├── tests/
│   ├── test_routing.*
│   ├── test_escalation.*
│   ├── test_policy_contract.*
│   ├── test_ledger_append_only.*
│   └── test_compiler_golden.*
└── docs/
    ├── architecture.md
    ├── attestation-limitations.md
    └── adapter-capabilities.md
```

實作語言不由本規格指定；`route()` 與 schema validation 必須可離線執行，且 reference implementation SHOULD 避免不必要的 runtime dependencies。

---

## 15. Fork Change Plan

### Phase 0 — Baseline verification

- Clone/fork 原 repo。
- `git fetch --tags --force`、`git tag -l`。
- 保存原始 main commit SHA。
- 建立 baseline snapshot：settings template、六 roles、policy block、installer。
- 不依 Releases UI 推論 tag 或版本。

### Phase 1 — Canonical model and L1 router

- 新增 schemas。
- 新增 canonical `routing.yaml`。
- 實作 pure `route(envelope, history)`。
- 實作 risk tag normalization 與 escalation ladders。
- 建立 L1 fixtures，先讓 CI 成為 blocking gate。

### Phase 2 — Claude adapter

- 將既有六 roles 轉成 generated artifacts。
- 將 `executor` 改為 `sonnet/high`。
- 新增 `senior-executor` 為 `opus/high`。
- 保留 `security-executor` 為 `opus/high`。
- 更新 policy：要求先形成 envelope，再呼叫 deterministic route。
- 維持 named-role model single source of truth。
- 更新 installer、collision check、backup、uninstall。

### Phase 3 — Ledger and attestation

- 實作 append-only records。
- 加入 configured／observed／unknown distinction。
- 偵測全域 subagent model override。
- 可用時接 hooks／transcript parser。
- attestation mismatch 使 run invalidated。

### Phase 4 — L2 classifier eval

- 建立自然語言 golden fixtures。
- 支援多次 headless runs。
- 產出 pass-rate／variance report。
- 初期不作 release blocker。

### Phase 5 — Codex adapter

- 先做 capability probe。
- 依實際能力編譯 target-specific artifacts。
- 對無法實現的 per-role model binding、tool controls 或 attestation 明確標示 degraded／unsupported。
- strict mode 不允許靜默降級。

---

## 16. Acceptance Criteria

v0.1 可被視為完成，至少需滿足：

1. 所有 canonical schemas 可驗證正例並拒絕反例。
2. L1 routing fixtures 全數通過。
3. 相同 envelope 與 history 的 router output 完全一致。
4. `migration` tag 可 deterministic 提升至 high risk。
5. security 任務不會路由到 Sonnet roles。
6. escalation 不會降級。
7. 所有 subagent roles 都不能 spawn。
8. verifier 沒有 write capability，且只能輸出 `CONFIRMED`／`REFUTED`。
9. named-role model binding 只有單一來源。
10. Ledger 可證明 append-only；更正使用 superseding record。
11. `CLAUDE_CODE_SUBAGENT_MODEL` 可被偵測並反映在 attestation status。
12. attestation unavailable 時回報 `UNKNOWN`，不偽稱 `MATCHED`。
13. Claude compiler golden outputs 穩定。
14. Codex compiler 會產生 capability report，且 strict mode 對 required capability 缺失 fail。
15. Installer 支援 dry-run、approval gate、backup、idempotent update 與 uninstall。
16. L2 runner 可輸出逐次結果與 variance，而非單一 pass/fail。

---

## 17. 已知限制

1. `classify()` 仍是 LLM in-band；只有 `route()` 能完全 deterministic。
2. transcript／hook model observation 是 best-effort，不是密碼學證明。
3. security pre-classifier 無法保證捕捉所有自然語言隱含 security 任務；L2 必須特別量測 security recall。
4. Codex adapter 可能無法達到 Claude adapter 的 feature parity。
5. Policy 與 tool controls 的實際效果仍受 target CLI 版本與 runtime 行為影響。
6. `UNKNOWN` attestation 不能被等同於 mismatch，也不能被等同於 match。

---

## 18. 尚待 implementation phase 決定的非核心參數

以下項目不阻擋 v0.1 核心規格，但必須在實作 PR 中明確決定：

- Ledger 實際 storage backend 與 retention policy。
- L2 每個 fixture 的 runs 數與 credits budget。
- L2 release threshold。
- Codex adapter 的實際 target files 與可觀測 metadata。
- Verifier 在非 security 任務是否維持 `medium` effort，或整體調整為 `high`。
- 任何 tag／version 策略，必須以 fork 時實際 `git tag -l` 結果為準。
