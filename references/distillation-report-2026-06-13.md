# 前沿自进化Agent框架蒸馏报告

> **日期**: 2026-06-13
> **目的**: 全面收集2025-2026前沿自进化Agent框架理论与独立开发者最佳实践，与skill-builder-v3现状逐一对照，识别可大改的架构缺口。
> **范围**: 18篇顶会/顶刊论文 + 12个开源框架 + Claude Code内部架构 + 7个独立创作者框架

---

## 一、收集的前沿框架全景

### 1.1 学术前沿（ICLR/ACL/NeurIPS/AAAI 2025-2026）

| 框架 | 出处 | 核心机制 | 蒸馏价值 |
|------|------|---------|---------|
| **Gödel Agent** | ACL 2025 | LLM递归修改自身逻辑，无预定义routine，仅凭高层目标引导 | ⭐⭐⭐⭐⭐ 规则自生成 |
| **EVE-Agent** | arXiv 2605.22905 | 每个训练样本携带可审查的evidence span；Evidence Verifier按边际准确率增益打分 | ⭐⭐⭐⭐⭐ 修复证据链 |
| **OpenSkill** | arXiv 2606.06741 | 零精调数据、零成功轨迹、零验证信号 → 从文档/仓库/网页自主获取锚点知识 | ⭐⭐⭐⭐ 零起始bootstrap |
| **Socratic-SWE** | arXiv 2606.07412 | 历史求解轨迹蒸馏为结构化agent技能；50.40% SWE-bench Verified | ⭐⭐⭐⭐⭐ 轨迹→规则蒸馏 |
| **Yunjue Agent** | arXiv 2601.18226 | 原位自进化 + 工具优先原则 + 并行批量进化 | ⭐⭐⭐⭐ 工具自动生成 |
| **SE-Agent** | NeurIPS 2025 | revision/recombination/refinement三操作优化推理轨迹 | ⭐⭐⭐ 轨迹优化 |
| **Mem²Evolve** | ACL 2026 | Experience Memory + Asset Memory互引导的共进化 | ⭐⭐⭐ 双记忆协同 |
| **Meta-Team** | arXiv 2605.29790 | 多智能体协作自进化，跨行为/协调/组织三层 | ⭐⭐⭐ 多agent编排 |
| **Co-EPG** | AAAI 2026 | Planning与Grounding的正反馈循环共进化 | ⭐⭐ GUI专项 |
| **DGM** | Sakana AI 2025 | 开放在档归档 + 经验基准验证替代形式证明 | ✅ 已蒸馏(v1.1) |
| **TAPO** | ICLR 2026 | 逐轮信用分配 + 规则自动降级 | ✅ 已蒸馏(v1.1) |
| **GEPA** | ICLR 2026 | 执行轨迹反馈——修完必须跑测试 | ✅ 已蒸馏(v1.1) |
| **HyperAgents** | ICLR 2026 | 跨技能元迁移——一个skill的发现自动应用到其他skill | ✅ 已蒸馏(v1.1) |
| **CRESCENT** | ACL 2025 | 共识增强——多次采样+多数表决 | ✅ 已蒸馏(v1.1) |
| **GVU** | Chojecki 2025 | 验证器SNR监控——SNR<1时暂停自改进 | ✅ 已蒸馏(v1.1) |

### 1.2 Claude Code内部架构（512K行TypeScript, 2026年3月泄露）

| 子系统 | 描述 | 蒸馏价值 |
|--------|------|---------|
| **7层权限系统** | ML分类器+正则白名单/黑名单+目录作用域+高风险操作显式确认 | ⭐⭐⭐ 权限分级 |
| **5层上下文压缩** | 工具输出截断→消息修剪→对话摘要→滑动窗口→语义去重 | ⭐⭐⭐ 记忆压缩 |
| **4个扩展机制** | MCP, Skills(CLAUDE.md), Hooks(生命周期回调), Plugins | ⭐⭐⭐⭐⭐ Hook集成 |
| **Subagent编排** | Git worktree隔离+并行任务分发+合并/丢弃 | ⭐⭐⭐⭐ 并行执行 |
| **仅追加存储** | 全状态持久化→从任意点崩溃恢复 | ⭐⭐⭐⭐ 崩溃恢复 |

### 1.3 独立创作者框架

| 框架 | 作者 | 关键创新 | 蒸馏价值 |
|------|------|---------|---------|
| **Claude Nexus** | asiflow | 32-agent层级体系+Bayesian信任校准+证据验证器+自动人才发掘 | ⭐⭐⭐⭐⭐ 多agent质量门控 |
| **ultragoal** | morphaxl | "做完"的可检查命令定义；fresh-eyes验证器不看工作过程；stop-hook门控 | ⭐⭐⭐⭐⭐ 独立验证 |
| **Compound Agent** | compound-agent | 5阶段复合学习+SQLite FTS5记忆索引+7个自动Hook | ⭐⭐⭐⭐ 语义记忆 |
| **GBase** | garyqlin | 递归自改进+镜像记忆+质量门控+Cognifold推理 | ⭐⭐⭐ 递归结构 |
| **Karpathy autoresearch** | karpathy | 单指标(val_bpb)爬山；5分钟/轮；AI编辑train.py，人编辑program.md | ⭐⭐⭐⭐ 统一适应度 |
| **CowAgent** | zhayujie | 三级记忆(context→daily→core) + Deep Dream夜间蒸馏 + 知识图谱 | ⭐⭐⭐ 记忆分层 |
| **RecursiveMAS** | RecursiveMAS | 专家-学习者递归交互 + Latent-space recursion + 蒸馏模式 | ⭐⭐⭐ 师生蒸馏 |

### 1.4 记忆架构前沿（2026）

| 系统 | 关键机制 | 蒸馏价值 |
|------|---------|---------|
| **Engram** | 双时态知识图谱 + 异步提取 + 精简检索(83.6%准确率, 8×更少token) | ⭐⭐⭐⭐⭐ |
| **NeuSymMS** | 神经提取+CLIPS规则引擎去重+subject-relation-value三元组+访问晋升 | ⭐⭐⭐⭐ |
| **Mem0** | 多级记忆(用户/会话/Agent) + 自动提取 + 26%更高准确率 | ⭐⭐⭐ |
| **Zep** | 时态知识图谱 + valid_at/invalid_at时间戳 + 溯源追踪 | ⭐⭐⭐ |

---

## 二、与skill-builder-v3的逐项对照

### 2.1 当前架构总览

```
                    ┌──────────────────────────────────┐
                    │ LAYER 3: ANCHOR                  │
                    │ 宪法验证 + 回滚 + GVU SNR门控     │
                    └──────────────┬───────────────────┘
                    ┌──────────────▼───────────────────┐
                    │ LAYER 2: REFLECT                 │
                    │ 元观察→质疑→提案→验证              │
                    │ + GEPA执行轨迹 + HyperAgents跨技能 │
                    └──────────────┬───────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │               LAYER 1: IMPROVEMENT LOOP             │
        │  GENERATE → EVALUATE → SELECT → INTEGRATE → VERIFY │
        │  + GEPA + TAPO + DGM                               │
        └─────────────────────────────────────────────────────┘
```

### 2.2 十大架构缺口

---

#### 🔴 缺口1: 无规则自生成机制（Gödel Agent缺口）

**现状**: 规则是`rules.py`中硬编码的5条元组。System 2可以提案新模式→写入`patterns-drafts.md`，但**没有机制将模式草案自动转化为可执行的System 1规则**（含检测正则+修复函数）。

**蒸馏来源**: Gödel Agent（ACL 2025）、Socratic-SWE（轨迹→技能蒸馏）、Yunjue Agent（工具优先进化）

**影响**: 系统检测到的"无规则匹配"发现永远无法被自动修复。新模式的发现→应用之间存在人肉断点。

**建议大改**:
- 新增 `engine/system2/rule_generator.py` —— 将System 2 deliberation输出+execution traces蒸馏为可执行的修复规则(name, pattern_regex, fix_function)
- 生成规则写入 `data/generated_rules/` (JSONL) 而非硬编码
- 生成规则有**试用期**(probation)：前5次修复需System 2审批，通过后自动设为auto_apply=True
- 类似Yunjue Agent从空工具库bootstrap通用能力的"工具优先"路径

```python
# 架构示意
class GeneratedRule:
    name: str
    pattern_regex: str
    fix_strategy: str        # LLM-generated fix code
    probation_remaining: int  # 试用修复次数
    source_traces: list       # 哪些execution traces启发了这个规则
    evidence_quality: float   # EVE-Agent风格的证据质量评分
```

---

#### 🔴 缺口2: 无修复证据链（EVE-Agent缺口）

**现状**: 修复报告只含`rule`和`action`字段，没有**证明修复有效的证据**。GEPA的execution trace捕捉了运行时输出但未将其形式化为一级证据概念。

**蒸馏来源**: EVE-Agent（每个训练样本携带可审查的evidence span）+ Evidence Verifier按边际准确率增益打分

**影响**: 无法区分"声称成功"和"确实成功"的修复。TAPO的fix_quality只跟踪Boolean成功/失败，不含**解释性证据**。

**建议大改**:
- 每个修复必须携带 `evidence_span` 字段：哪个源文件/测试输出/日志证明了修复有效
- 新增 `Evidence Verifier` 检查：证据是否真的支持修复声明？
- GEPA execution traces已隐式捕获此信息——只需形式化为一级字段

```python
fix_result = {
    "applied": True,
    "rule": "add-utf8-header",
    "evidence_span": {
        "type": "compile_check",
        "command": "python -c 'compile(open(...).read(), ..., \"exec\")'",
        "expected": "exit 0",
        "actual": "exit 0",
        "verdict": "evidence_consistent"  # or "evidence_inconsistent"
    }
}
```

---

#### 🔴 缺口3: 无独立验证器（Ultragoal缺口）

**现状**: `verify_execution()`在与修复相同的进程中运行验证命令。Anthropic的研究清楚表明：**知道修复过程的验证器不如不知道修复过程的独立验证器**。

**蒸馏来源**: ultragoal（fresh-eyes verifier——不知道工作过程的独立验证子智能体）、Claude Code subagent模式

**影响**: 自我验证的盲点无法被检测。修复者永远能找到让自己满意的验证方式。

**建议大改**:
- `verify_execution` 应启动**独立子进程/子agent**进行验证
- 独立验证器只知道"修复前状态"和"修复后状态"，不知道修复的具体方式
- 如果独立验证器的判断与修复者不一致 → 触发System 2深度审查

```python
def verify_execution_independent(skill_name, integration_result):
    """独立验证：启动子agent，只给它before/after代码，不告诉它怎么修的。"""
    # 独立agent重新运行全部测试，给出独立的pass/fail判断
    independent_verdict = spawn_subagent(
        f"验证skill '{skill_name}'在修改后是否功能正常。"
        f"不要假设修改是正确的——从头独立验证。"
    )
```

---

#### 🔴 缺口4: 无语义记忆检索（Engram缺口）

**现状**: insights是glob扫描的JSON文件。没有全文搜索（FTS），没有语义检索（embedding），没有去重，没有时态有效性。

**蒸馏来源**: Engram（双时态知识图谱+精简检索83.6%准确率）、Compound Agent（SQLite FTS5索引）、NeuSymMS（神经提取+规则去重）

**影响**: 随insight数量增长，检索效率线性下降。类似insight被重复写入。过时的insight永久保留。

**建议大改**:
- `memory.py` 新增SQLite FTS5全文索引层（轻量级，无需额外依赖）
- 可选的embedding语义相似度检索（需`pip install sentence-transformers`）
- 时态有效性：insight带 `valid_until`，过期自动归档
- 自动去重：相似insight合并confidence提升

```python
class SemanticMemoryIndex:
    """三层索引：FTS5全文 → embedding语义 → JSON原文件"""
    def search(self, query, top_k=5, method="hybrid"):
        # 混合检索：FTS5关键词 + 可选embedding相似度
```

---

#### 🔴 缺口5: 无Hook集成（Claude Code缺口）

**现状**: 作为独立Python CLI运行，不利用Claude Code的Hook系统。每次修复需要用户主动运行 `python engine/loop.py --target <skill>`。

**蒸馏来源**: Claude Code Hook系统（SessionStart/SubagentStop/PreToolUse/PostToolUse/Stop）、Compound Agent（7个自动Hook）

**影响**: 不是"自主agent"，是"手动工具"。真正的自进化应该**自动触发**而非等待用户命令。

**建议大改**:
- 在 `~/.claude/settings.json` 中配置以下Hooks:
  - `SessionStart`: 预加载相关insights，注射当前skill的已知问题到上下文
  - `PostToolUseFailure`: 捕捉工具失败 → 自动写入event → 触发轻量诊断
  - `Stop`: 如果本轮有修复活动 → 自动运行loop收敛检查 → 记录结果
- 保留CLI入口以便手动调用
- 这是从"工具"到"自治agent"的关键跨越

```json
// .claude/settings.json hooks example
{
  "hooks": {
    "SessionStart": [
      {"command": "python engine/loop.py --target ${SKILL_NAME} --gvu-check"}
    ],
    "PostToolUseFailure": [
      {"command": "python engine/memory.py write-event ${SKILL_NAME} --type fix --data '{\"failure\": true}'"}
    ],
    "Stop": [
      {"command": "python engine/loop.py --target ${SKILL_NAME} --dry-run"}
    ]
  }
}
```

---

#### 🟡 缺口6: 无元生产力追踪（HGM缺口）

**现状**: TAPO追踪即时修复成功率，但**不追踪下游级联效应**。一个修复成功了但导致3次回滚 → TAPO看不到这种相关性。

**蒸馏来源**: HGM（Clade Metaproductivity——衡量所有后代的集体产出，而非仅即时性能）

**影响**: 不能区分"暂时有效但长期有害"和"持续有效的修复"。

**建议大改**:
- 新增 `descendant_quality_score`: 修复A是否阻塞或启用了后续修复B/C/D？
- 扩展TAPO的fix_fingerprints追踪到多跳因果链
- 如果一个修复导致的下游回滚数超过阈值 → 即使即时成功也标记为有害

---

#### 🟡 缺口7: 无零起始Bootstrap（OpenSkill/Yunjue缺口）

**现状**: 需要patterns.md、scanner规则、rules.py全部预先存在。新创建的skill没有免疫系统。

**蒸馏来源**: OpenSkill（零精调数据bootstrap——从文档/仓库/网页自动获取锚点知识）、Yunjue Agent（从空工具库bootstrap）

**影响**: 新skill的"免疫系统"建立完全依赖人工。限制了skill-builder作为通用自进化引擎的适用范围。

**建议大改**:
- 新增 `bootstrap_new_skill(skill_name)` —— 扫描skill结构 → 生成初始patterns.md模板
- 使用OpenSkill风格的开放世界知识获取：查询skill文档中引用的工具 → 搜索常见anti-pattern → 生成初步检测规则
- 初始规则全部设为probation模式

---

#### 🟡 缺口8: 无并行候选评估（Yunjue缺口）

**现状**: Loop是严格顺序的——每轮只有一个候选修复方案。

**蒸馏来源**: Yunjue Agent（并行批量进化——多个工具并行合成/验证/优化）、Claude Code（Git worktree隔离+并行任务分发）

**影响**: 对于需要尝试多种修复策略的问题，顺序loop效率低下。

**建议大改**:
- `SELECT`步骤生成N个并行候选（在隔离的git worktree中）
- 每个候选独立运行INTEGRATE+VERIFY
- 取Pareto最优候选合入主干
- 类似于Yunjue的Parallel Batch Evolution

---

#### 🟢 缺口9: 无统一适应度分数（Karpathy缺口）

**现状**: 有多个指标(fndings_count, verifier_snr, execution_trace_pass_rate, convergence_rounds)但无统一适应度分数。无法进行爬山优化。

**蒸馏来源**: Karpathy autoresearch（单一val_bpb指标驱动全部优化决策）

**影响**: design_params的调优完全靠人工直觉，无法自动爬山。

**建议大改**:
- 定义 `unified_fitness = w1×(-findings_count) + w2×verifier_snr + w3×trace_pass_rate - w4×convergence_rounds`
- 每N轮尝试微调design_params → 测量unified_fitness变化 → 保留改进

---

#### 🟢 缺口10: 无崩溃恢复（Claude Code缺口）

**现状**: 事件是仅追加的，但Loop本身没有resume-from-crash机制。如果进程在Round 3/10被杀死 → 必须从头开始。

**蒸馏来源**: Claude Code（仅追加会话存储 → 从任意点恢复）、Engram（容错长程运行）

**影响**: 长运行（10+轮）中的crash代价高。

**建议大改**:
- 每轮开始前写 `data/loop_state.json`（当前轮数、剩余发现、活跃proposals）
- 启动时检测是否有未完成的loop → 自动resume
- 完全复制Claude Code的crash recovery模式

---

## 三、架构改造优先级

### 🚨 Tier 1: 结构性质变（必须优先做）

| 序号 | 改造 | 对应缺口 | 新增模块 | 改造文件 |
|------|------|---------|---------|---------|
| **P1** | 规则自生成引擎 | 缺口1 | `engine/system2/rule_generator.py` | `rules.py`, `loop.py`, `reflect.py` |
| **P2** | 修复证据链 | 缺口2 | `engine/evidence.py` (Evidence Verifier) | `loop.py:verify_execution`, `memory.py` |
| **P3** | 独立验证器 | 缺口3 | 子agent验证协议 | `loop.py:verify_execution` |
| **P4** | 语义记忆索引 | 缺口4 | `engine/memory_index.py` (SQLite FTS5) | `memory.py` |

### ⚡ Tier 2: 能力性质变（第二波）

| 序号 | 改造 | 对应缺口 | 新增模块 |
|------|------|---------|---------|
| **P5** | Hook集成 | 缺口5 | `.claude/settings.json` hooks配置 |
| **P6** | 元生产力追踪 | 缺口6 | `memory.py:descendant_quality` |
| **P7** | 零起始Bootstrap | 缺口7 | `engine/bootstrap.py` |

### 📈 Tier 3: 效率性质变（第三波）

| 序号 | 改造 | 对应缺口 | 新增模块 |
|------|------|---------|---------|
| **P8** | 并行候选评估 | 缺口8 | `engine/parallel.py` (worktree isolation) |
| **P9** | 统一适应度爬山 | 缺口9 | `engine/hillclimb.py` |
| **P10** | 崩溃恢复 | 缺口10 | `engine/checkpoint.py` |

---

## 四、改造后的目标架构

```
                          ┌──────────────────────────────────────────┐
                          │ LAYER 3: ANCHOR (不变)                   │
                          │ 宪法验证 + 回滚 + GVU SNR门控            │
                          │ + EVE Evidence Verifier (NEW)            │
                          └──────────────┬───────────────────────────┘
                          ┌──────────────▼───────────────────────────┐
                          │ LAYER 2: REFLECT (增强)                  │
                          │ 元观察→质疑→提案→验证                     │
                          │ + Rule Generator (Gödel Agent) → auto-gen │
                          │ + HyperAgents跨技能 (不变)                │
                          │ + HGM Metaproductivity tracking (NEW)    │
                          └──────────────┬───────────────────────────┘
              ┌──────────────────────────▼──────────────────────────────┐
              │            LAYER 1: IMPROVEMENT LOOP (增强)             │
              │  GENERATE → EVALUATE → SELECT → INTEGRATE → VERIFY    │
              │                                                        │
              │  ┌─ EVALUATE: + Evidence span check (NEW)              │
              │  ├─ SELECT: + Karpathy hill-climb on design_params     │
              │  ├─ INTEGRATE: + Crash checkpoint save (NEW)           │
              │  └─ VERIFY: + Fresh-eyes independent verifier (NEW)    │
              │                                                        │
              │  New modes:                                            │
              │  ├─ PARALLEL: N candidates in git worktrees (NEW)     │
              │  └─ BOOTSTRAP: zero-start skill initialization (NEW)   │
              └────────────────────────────────────────────────────────┘

          ┌──────────────────────────────────────────────────────────┐
          │            MEMORY LAYER (重构)                            │
          │  Tier 1: Event Log (不变)                                 │
          │  Tier 1.5: SQLite FTS5 Index (NEW) — 语义检索            │
          │  Tier 2: Insights + Evidence (增强)                       │
          │  Tier 2.5: Evidence Verifier (NEW) — EVE-Agent pattern   │
          │  Tier 3: Metrics + Metaproductivity (增强)                │
          │  Tier 4: Generated Rules (NEW) — Gödel Agent pattern     │
          └──────────────────────────────────────────────────────────┘

          ┌──────────────────────────────────────────────────────────┐
          │            HOOK LAYER (新增)                              │
          │  SessionStart → pre-load insights                        │
          │  PostToolUseFailure → auto-diagnose                       │
          │  Stop → auto-loop check                                  │
          └──────────────────────────────────────────────────────────┘
```

---

## 五、风险与迁移策略

### 5.1 关键风险

1. **规则自生成的幻觉风险**: LLM生成的fix function可能引入新的bug。缓解：必须在probation模式下运行5次，且全部通过独立验证器审查后才可晋升。

2. **语义记忆索引的依赖膨胀**: embedding检索需要额外依赖。缓解：FTS5是Python内置的sqlite3模块，无需额外安装。embedding为可选特性。

3. **并行候选评估的worktree开销**: git worktree创建有磁盘开销。缓解：仅在System 2触发时（僵局检测）才启用并行模式。

4. **Hook集成的环境依赖**: 依赖Claude Code的Hook系统。缓解：所有功能同时保留CLI入口，Hook是附加的自动触发路径。

### 5.2 迁移路径

```
Phase 1 (本迭代): P1+P2+P3+P4 → v1.2
  规则自生成 + 修复证据链 + 独立验证器 + 语义记忆索引
  
Phase 2 (下迭代): P5+P6+P7 → v1.3
  Hook集成 + 元生产力追踪 + 零起始Bootstrap

Phase 3 (远期): P8+P9+P10 → v2.0
  并行候选 + 统一适应度爬山 + 崩溃恢复
```

---

## 六、宪法影响评估

### 现有法则是否需要修改？

| 法则 | 影响 | 需要修正？ |
|------|------|-----------|
| 第零法则(可实现性) | 新增4个模块需要纳入可实现性审计 | ⚠️ 扩展审计范围 |
| 第一法则(自指闭环) | 规则自生成→新规则对skill-builder-v3自身的适用性需验证 | ✅ 不变 |
| 第二法则(记录驱动) | 新增evidence field和generated_rules → 扩展事件类型 | ⚠️ 扩展event_types |
| 第三法则(验证优先) | 独立验证器增强了验证→强化而非削弱 | ✅ 不变 |
| 第四法则(人在回路) | 规则自生成的probation机制保持人在回路 | ✅ 不变 |
| 第五法则(不破坏基础) | pre-modify检查范围需扩展到新模块 | ⚠️ 扩展检查项 |

### 宪法修正建议

在第三法则中增加：
```
6. (v1.2) 独立验证——修复验证必须由独立子进程执行，不得与修复代码共享执行上下文
```

在第二法则中增加event_types：
```
"evidence_span",     // (EVE-Agent) 修复证据——为什么这项修复被认为有效
"rule_generated",    // (Gödel Agent) 新规则自动生成
```

---

## 七、总结

skill-builder-v3的v1.1六个框架蒸馏打下了坚实基础，但2026年的前沿发展揭示了一个清晰的方向：

**从"手动工具"到"自治Agent"的跨越。**

当前架构的核心瓶颈是**规则是硬编码的**——5条System 1规则限制了系统的自进化天花板。Gödel Agent、Socratic-SWE、Yunjue Agent都证明了**自动生成执行规则/工具**是从"优化已有"到"创造新能力"的质变。

其次是**记忆的线性化**——JSON glob扫描不随数据增长而scale。Engram和Compound Agent的实践证明了轻量级索引（SQLite FTS5）可以在不引入新依赖的情况下实现质的飞跃。

最后是**验证的独立性**——ultragoal和Claude Code的独立subagent验证模式是Anthropic自己证明比self-critique更有效的策略。

这三个方向（规则自生成、语义记忆、独立验证）构成v1.2的核心改造成本，预计新增3-4个模块，改造5-6个现有文件。其余7个缺口可在v1.3和v2.0中逐步纳入。

---

**报告作者**: skill-builder-v3 系统自动分析
**审查状态**: 待人工审批
**下一步**: 人工确认优先级后，启动 Phase 1 实施计划
