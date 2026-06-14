# 模式库 — 迭代审计检查清单

> 从多轮自改进迭代中提取。每次迭代前先对照这些结构性模式扫一遍。
> 
> 模式来源：人工编写 + System 2 自动发现提案（见 patterns-drafts.md）
> 被推翻的模式：见 patterns-deprecated.md
> 演化历史：见 pattern-evolution.jsonl
>
> **v1.1 新增 (2026-06-13):** 模式 6-10 来自前沿框架蒸馏（原 8-12，v1.2 重编号）
> **v1.2 新增 (2026-06-13):** 模式 12-14 来自 20 轮自进化实验——指标死值/累计倒退/置信悬崖
> **v1.2 移除:** 模式 3/4/5——纯概念模式，15 轮零 finding，无 scanner 规则支持

---

## 核心锚点 — 自进化不能违背的四条底线

框架修改前，逐条验证。任何一条不成立，拒绝修改。

| # | 锚点 | 实际验证方式（非声称，是代码实际执行的） |
|---|------|---------|
| **A** | **通用性：同一流程对任意目标对等。** 不能给 skill-builder 自己留特殊路径。 | `grep -rn "skill-builder-v3" engine/` 条件分支仅在 {anchor, patterns, scanner, realizability}.py 中合法。CLI 默认值不含自身硬编码。|
| **B** | **记录驱动：每个修复必须留下文件证据。** | `memory.py audit-trail` 检查事件日志完整性；`realizability.py` 检查记录基础设施存在性。 |
| **C** | **内循环收敛：不允许无限迭代。** | 连续 stop_rounds 轮零扫描发现 = 停止。**诚实警告：零发现 ≠ 零问题，可能是扫描器盲区。** `realizability.py` 定期审计扫描器覆盖率。 |
| **D** | **自指闭环：能优化别人 = 能优化自己。** | 自身 manifest.json / patterns.md / known-issues.md 使用与任意 skill 完全相同的格式和校验逻辑。`realizability.py` 检查无特殊豁免。 |

---

## 模式 1：分布式状态

**症状：** 同一个概念散落在多个文件中，每次修改只更新了部分。

**对策：** 新增字段/检查项时，搜索涉及该概念的所有文件，一次性同步。

**关联：** 原模式 5 合并至此——修复完成后必须追踪该概念在代码中的每一个位置。修 A 必须检查 B、C、D。

---

## 模式 2：硬编码数字

**症状：** 文档里写死数字，代码增删后数字过时。

**解决方案：** 永远不用数字。用描述："全部 [OK]"、"所有基础设施"。

---

## 模式 4：分散实现

**症状：** 同一个概念在两个位置各自独立实现。

**对策：** 每个概念只在一个地方实现，其他地方通过调用引用它。

---

## 模式 5：语言一致性漂移

**症状：** 中文环境下用户可见输出中出现英文短句。

**对策：** 
1. 所有脚本必须有 UTF-8 stdout 强制头
2. 每次迭代前扫描英文哨兵
3. 新脚本默认中文输出

---

## 模式 6：GEPA 假修复 (v1.1 新增)

**来源:** GEPA (Nous Research, ICLR 2026) — execution trace feedback

**症状：** 修复声称成功（fix applied=true），但实际运行修复后的代码仍然报错。

**检测方式:**
- `memory.py:read_events(skill, "execution_trace")` 检查 trace 中的 success 字段
- `memory.py:calculate_gvu_snr()` 监控 generator_noise / verifier_snr

**对策：**
1. 每次 `integrate_fixes` 后必须 `verify_execution`——实际跑一次修复后的代码
2. 执行失败的修复自动记录 fix_quality=false
3. 连续 3 个以上 execution_trace 失败 → System 2 介入

---

## 模式 7：TAPO 规则退化 (v1.1 新增)

**来源:** TAPO/ReVeal (Jin et al., ICLR 2026) — per-turn credit assignment

**症状：** 某个 System 1 规则曾经好用，但近期修复成功率持续下降。

**检测方式:**
- `memory.py:get_fix_quality(skill, rule)` 检查 last_10_rate
- 低于 50% → 触发自动降级

**对策：**
1. `rules.py:check_and_maybe_demote_rules()` 自动关闭低质量规则的 auto_apply
2. 降级后需最近 10 次成功率回到 70%+ 才能重新启用
3. 被降级的规则仍然可以手动执行（`--all` 标志）

---

## 模式 8：GVU 系统不稳定 (v1.1 新增)

**来源:** GVU Operator (Chojecki, 2025) — Variance Inequality

**症状：** 验证器 SNR ≤ 生成器噪声。系统修复速度跟不上产生新问题的速度。

**检测方式:**
- `memory.py:calculate_gvu_snr()` → generator_noise > verifier_snr

**对策：**
1. SNR < 1 时，trusted 模式下 GVU 门控阻止自动修复
2. 触发 System 2 元审计
3. 需要人工校准后才恢复自动修复

---

## 模式 9：跨技能传染 (v1.1 新增)

**来源:** HyperAgents (Meta, ICLR 2026) — cross-domain meta-transfer

**症状：** 同一个 pattern 出现在 3+ 个独立的 skill 中。

**检测方式:**
- `reflect.py:cross_skill_scan()` 扫描所有 skill
- `memory.py:global_insights` 记录跨技能模式

**对策：**
1. 在 skill-builder-v3 的 patterns.md 中作为通用模式添加
2. 一次性对所有受影响的 skill 应用修复
3. 全局洞察记录在 `data/global_insights/`

---

## 模式 10：DGM 局部最优陷阱 (v1.1 新增)

**来源:** Darwin Godel Machine (Zhang et al., ICLR 2026) — archive diversity

**症状：** 系统在多轮迭代中保持收敛，但 findings 模式完全相同——可能陷入局部最优。

**检测方式:**
- `loop.py:archive_if_diverse()` 连续 N 次未创建新存档

**对策：**
1. 从 archive 中选取一个多样性版本，在测试集上评估
2. 如果多样性版本表现不差于当前版本 → 采纳多样性版本
3. 定期（每 archive_interval 轮）强制多样性检查

---

## 模式 11：扫描器盲区 (v1.1 新增，可实现性审计发现)

**来源:** 宪法可实现性审计 (`engine/realizability.py`)

**症状：** 零发现 = 停止，但零发现可能是扫描器只覆盖了已知的 8 类语法问题，
而完全不知道以下问题的存在：
- API 版本不兼容（如 yt-dlp 参数变更）
- 网络/超时/DNS/代理问题
- 并发/竞态条件
- 配置文件格式漂移
- 依赖的传递依赖冲突
- 权限问题
- 编码问题（非 UTF-8 文件）
- 大文件 / 内存问题

**诚实声明：** 这些是扫描器做不到但真实存在的问题领域。宪法不假装能扫描。

**检测方式:**
- `python engine/realizability.py` → audit_scanner_blindness 检查
- 扫描器覆盖率审计: 11 条规则覆盖文件内容扫描 + 3 条指标扫描 (v1.2) | 至少 8 类已知问题在外

**对策：**
1. 收敛标准不应仅为"零发现"——应增加"最近一次扫描器覆盖率审计通过"
2. 已知盲区写入 known-issues，标注 `@scanner_blindspot`
3. 每次遇到扫描器盲区内的新问题 → 新增检测规则到 scanner.py → 缩减盲区
4. 盲区永远存在——诚实承认这一点比假装全覆盖更有价值

---

## 模式 12：指标死值 (v1.2 新增)

**来源:** skill-builder-v3 20轮自进化实验观测 (2026-06-13)

**症状：** 健康指标连续 N≥5 次测量值完全相同，且该值不是合法稳态（如 findings=0 是合法稳态，SNR=1.0 不是）。
统计上不可能自然发生，说明计算公式中存在恒等映射或硬编码常量。

**检测方式:**
- 读取 `data/metrics/*.jsonl` → 对每条指标计算最近 N 个值的标准差
- 标准差 = 0 且值不是已知合法稳态 → 报告 `metric-dead-value`

**已知历史案例:**
- SNR=1.0 连续 20 次测量不变（概率 < 10⁻⁶）
- false_convergence=1.0 连续 15 次测量不变
- ask_gate confidence=0.0 连续 17 次测量不变（从悬崖后未恢复，详见模式 14）

**对策：**
1. 标准差为 0 的指标自动标记 `@metric_suspect`
2. 触发 System 2 Reflect：检查该指标的计算代码是否包含硬编码返回值
3. 在指标定义中声明合法稳态值域——含期待范围外的值也触发警报
4. 修复后必须观测到该指标产生至少 2 个不同值，才算验证通过

---

## 模式 13：累计值倒退 (v1.2 新增)

**来源:** skill-builder-v3 20轮自进化实验观测 (2026-06-13)

**症状：** 累计字段（如 total_fixes、total_findings）在任何一次保存时小于前值。
累计值只应单调不减。

**已知历史案例:**
- total_fixes 四连降: 86 → 78 → 60 → 40 → 24（5轮内掉 72%）
- 疑似 TAPO 信用降级在批量删除低质量修复记录，或快照窗口偏移

**检测方式:**
- 对比当前 `loop_state.json` 和最新 snapshot 中的累计字段
- 当前值 < 快照值 → 报告 `cumulative-regression`

**对策：**
1. 修复记录的删除必须记录 `@deleted` 原因到 audit trail
2. 累计字段单调性检查纳入每次 integrate 步骤
3. 发现倒退时自动从快照恢复丢失的记录

---

## 模式 14：置信度悬崖 (v1.2 新增)

**来源:** skill-builder-v3 20轮自进化实验观测 (2026-06-13)

**症状：** 系统自信度在单次事件中骤降超过 0.5，且此后 N≥5 轮不恢复。
从过度自信（自称 100%）跳到完全否定（0%），两个极端都不健康。

**已知历史案例:**
- ask_gate self_confidence: 1.0 → 0.0（12:04），此后持续 17 轮 0.0

**检测方式:**
- 读取 `data/metrics/ask_gate_confidence_gap.jsonl`
- 检测连续两点的差值 > 0.5 → 标记悬崖时间点
- 悬崖后连续 N≥5 轮未恢复 → 报告 `confidence-cliff-unresolved`

**对策：**
1. 悬崖发生后第 3 轮自动触发重新校准——请求人工确认一次简单修复
2. 置信度恢复机制: 如果最近 3 次实际修复全部成功，允许置信度逐步回升
3. 禁止置信度在 0 和 1 之间直接跳变——强制 sigmoid 平滑

---

## 模式 15：事件 Schema 漂移 (v1.2 新增, 2026-06-13 自指修复发现)

**来源:** skill-builder-v3 自改进——修复模式 12/14 时发现的新问题

**症状：** 两个模块对同一 event_type 的 data 结构有不同假设。
`write_event` 处使用的 key 与 `read_events` 处理处期望的 key 不一致，
造成读取侧始终获得空值/默认值，形成"静默失败"——无报错，但数据流完全断裂。

**已知历史案例:**
- `loop.py:integrate_fixes()` 写入 `{"fixes": fix_report, "snapshot": ...}`
- `ask_gate.py:check_calibration()` 读取时查找 `fixes_report` / `fix_report`，永远找不到 `fixes`
- 结果: `self_confidence = 0.0, actual_accuracy = 0.0` — 43 轮未变（模式 12 死值）
- 根因: 写入侧和读取侧的 key 命名不一致，无编译时检查

**检测方式:**
- 对每种 event_type，收集所有写入调用的 data keys 和所有读取处期望的 keys
- 交集为空 → 报告 `event-schema-mismatch`
- 对写入侧存在的 key 但无读取侧匹配 → 死数据（可能故意遗弃，警告）
- 对读取侧期望的 key 但无写入侧匹配 → 死读取（必然返回默认值，告警）

**对策：**
1. 每种 event_type 在 `references/event-schemas.md`（新建）中声明其 data 契约
2. `scanner.py` 新增规则 `schema-drift-check`: 对比写入/读取侧的 key 使用
3. 添加新 event_type 时必须在 schema 文档中声明，否则 scanner 报告
4. 现有 fix/scan/integrate/execution_trace 四种事件加入一致性检查
