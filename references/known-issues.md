# 已知问题与修复

> 格式：`<!-- @ttl version_added="X.Y" last_referenced_iso="YYYY-MM-DD" reference_count="0" -->`
> 每个条目带 TTL 元数据，由 engine/memory.py 管理自动衰减。

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 闭合 Pattern 15 事件 Schema 文档缺口
- **环境：** Windows 11 / Python 3.13
- **现象：** `references/event-schemas.md` 不存在——Pattern 15 在 2026-06-13 自指修复中发现 Schema 漂移问题，对策中明确要求创建此文件，但一直未实施。`manifest.json` 的 `file_inventory.references` 也未列出此文件。
- **根因：** Pattern 15 的对策被写在文档中但无人执行——对策 1 到 4 中的第 1 条就是"新建 `references/event-schemas.md`"。对策优先级的疏忽：文档作为"修复后产物"而非"修复本身"被忽略。
- **修复：** 
  - 新建 `references/event-schemas.md`——14 种事件类型的完整 data 契约声明（写/读侧模块、data schema、契约规则、历史漂移记录）
  - `manifest.json` 的 `file_inventory.references` 新增 `event-schemas.md`
- **验证：** `python engine/anchor.py verify-self` 通过；`python engine/loop.py --target skill-builder-v3` 稳定收敛

### [2026-06-13] 项目初始化
- **环境：** Windows 11 / Python 3.13
- **现象：** 新项目——免疫系统尚在建设中
- **修复：** 初始化 known-issues.md、changelog-archive.json、patterns.md
- **验证：** `python engine/anchor.py verify-self` 通过

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 事件 Schema 漂移 + GVU SNR 恒等映射 + 缺失 GEPA 执行轨迹

- **环境：** Windows 11 / Python 3.13
- **现象：** 
  1. Scanner 检测 ask_gate confidence gap 从 1.0→0.0 悬崖后 44 轮未恢复 (模式 14)
  2. Scanner 检测 verifier SNR 连续 5+ 轮精确 = 1.0 (模式 12)
  3. Scanner 检测 ask_gate confidence gap 连续 5+ 轮精确 = 0.0 (模式 12)
- **根因分析：**
  1. **Schema 漂移:** `ask_gate.py:check_calibration()` 读取 fix 事件时期望 `data.fixes_report` / `data.fix_report`，但 `loop.py:integrate_fixes()` 写入时使用 key `"fixes"`。读取侧永远落到 fallback → `data` 本身，而 `data.get("applied", 0)` 找不到→ 返回 0。导致 self_confidence=0, actual_accuracy=0, gap=0（看起来像"校准良好"的假象）。
  2. **GVU 恒等映射:** `memory.py:calculate_gvu_snr()` 的 verifier_snr 公式：`correct_fixes = total_fixes - total_rollbacks` → 当 rollbacks=0 时恒等于 1.0。缺少独立验证信号（GEPA execution_trace）的融合。
  3. **缺失执行轨迹:** `verify_execution_independent()` 只在 disagreement 时写入 execution_trace——按 GEPA 原则，所有修复都应记录。
- **修复（3 文件）：**
  - `engine/ask_gate.py`: 添加 `data.get("fixes", ...)` 到读取链路；只统计 applied>0 的修复；添加 `insufficient_data` flag 区分"数据不足"和"校准良好"
  - `engine/memory.py:calculate_gvu_snr()`: 融合 execution_trace 事件作为独立验证信号；当无 trace 时标记 `verifier_source: "unverified"` 而非默认 1.0
  - `engine/loop.py:verify_execution_independent()`: 无条件写入 execution_trace（GEPA 原则）
  - `references/patterns.md`: 新增模式 15——事件 Schema 漂移检测
- **验证：** 
  - Constitution verify-self 通过
  - ask_gate 正确返回 insufficient_data=True（仅 1 次 applied>0，需 ≥3）
  - GVU 正确回报 verifier_source="unverified"（仅 1 个 trace，需 ≥3 才能融合）
  - 新循环运行后生成新指标——scanner findings 将在足够数据积累后自动消除

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 四阶段验证门被 Red-Team 穿透 (4/5)

- **环境：** Windows 11 / Python 3.13
- **现象：** Red-Team 审计发现 5 个已知缺陷中仅 1 个被捕获 (G1 语法)，4 个穿透验证门。
  穿透的缺陷：R2 文件幻觉引用、R3 不稳定输出、R4 严重度伪装、R5 噪声淹没。门健康度=20%。
- **根因分析：**
  - **G2 (扎根):** 仅检查 `files_changed` 中的路径是否存在，不扫描文件内容中的引用。R2 在注释中引用不存在的文件 → 漏过。
  - **G3 (稳定性):** 使用 `evidence.command`（py_compile）检查稳定性，而编译命令永远确定 → R3 漏过。
  - **G4 (语义):** 仅比较 critical 计数 (pre vs post)，不检测严重度重命名 (critical→warning) 或噪声注入 (500行+无用内容) → R4/R5 漏过。
- **修复（1 文件，3 处）：**
  - `engine/trace_distiller.py: G2_grounding` — 新增文件内容路径扫描，正则匹配 `[\w./-]+\.(py|js|...)` 并验证目标存在
  - `engine/trace_distiller.py: G3_stability` — 跳过静态分析命令 (py_compile/mypy/flake8)，fallback 到直接运行 .py 文件两次比对输出
  - `engine/trace_distiller.py: G4_semantic` — 严重度伪装检测 (critical↓ + warning↑)；噪声淹没检测 (文件 >200 行但 findings 减少 ≤2)
- **验证：** Red-Team 重新审计 → 5/5 全部捕获，门健康度=100%

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] descendant_links 污染 fix quality + GVU 干净状态误报

- **环境：** Windows 11 / Python 3.13
- **现象：**
  1. `descendant_links` 显示修复质量 0% (0/20) — 但它是因果图记录，不是质量记录
  2. GVU 在系统干净 (0 findings, 0 rollbacks) 时仍报告 `[!!] SNR 未独立验证`
- **根因：**
  1. `descendant_links.jsonl` 存在 `fix_quality/` 目录但 schema 完全不同 (parent_rule/child_rule/relationship vs success)
  2. `memory.py:get_all_fix_qualities()` 对所有 `fix_quality/*.jsonl` 无差别读取
  3. GVU verdict 未区分 "无回滚+干净" (健康) 和 "有回滚+无trace" (危险)
- **修复（1 文件）：**
  - `engine/memory.py:get_all_fix_qualities()` — 排除 `descendant_links` 文件
  - `engine/memory.py:calculate_gvu_snr()` — 干净状态 (0 rollback + 0 findings) → `[OK]`；区分 no_data / unverified / clean / blended

<!-- @ttl version_added="1.1" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] v1.1 六大前沿框架蒸馏
- **环境：** Windows 11 / Python 3.13 / bash (Git Bash)
- **现象：** v1.0 架构骨架完整，但缺少学界前沿的自进化机制
- **蒸馏框架：**
  - GEPA 执行轨迹验证（loop.py: verify_execution）
  - HyperAgents 跨域迁移（reflect.py: cross_skill_scan, memory.py: global_insights）
  - CRESCENT 共识增强（challenger.py: build_consensus_prompts, deliberator.py: build_consensus_prompts）
  - TAPO 逐轮信用分配（rules.py: check_and_maybe_demote_rules, memory.py: fix_quality）
  - GVU 验证器 SNR 门控（memory.py: calculate_gvu_snr, loop.py GVU gate）
  - DGM 多样性存档（loop.py: archive_if_diverse）
- **修复：** 10 个文件修改/增强（engine/memory.py, engine/loop.py, engine/reflect.py, engine/system1/rules.py, engine/system1/patterns.py, engine/system2/challenger.py, engine/system2/deliberator.py, skill.md, manifest.json, references/patterns.md）
- **验证：** patterns.md 新增模式 8-12，constitution 五法则保持不变
- **备注：** skills/graph.py 和 skills/registry.py 后续已实现（manifest 声明但未实现）

<!-- @ttl version_added="1.1" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 宪法可实现性统一修正
- **环境：** Windows 11 / Python 3.13
- **现象：** 宪法 v1.0 中有多条声明与引擎实际能力不符：
  1. 第一法则声称 `grep -rn 'skill-builder-v3'` 不能在条件分支中出现 → 但 anchor/scanner/patterns 必须在条件分支中检查自身
  2. 第三法则声称检查"所有"模块的 --help → 实际仅检查关键模块
  3. 第五法则声称 4 项检查全部执行 → 实际 2 项完整 + 2 项部分
  4. CLI 默认值全部硬编码为自身名称 → 违反第一法则
  5. GEPA 验证命令硬编码 `python` → macOS/Linux 上可能找不到
- **根因：** 宪法书写时是以"理想"而非"实际能做到"为标准
- **修复：** 
  - 宪法新增第零法则（可实现性）+ 每条法则增加"诚实声明"段落
  - 宪法第一法则区分"宪法执行代码"和"真正的特殊路径"
  - 第三/第五法则措辞修正为"关键模块"而非"所有模块"
  - 新增 `engine/realizability.py` 宪法可实现性审计
  - 移除 5 处 CLI 默认值中对自身名称的硬编码
  - `loop.py` 中 GEPA 验证命令 `python` → `sys.executable`
  - `memory.py` 全局路径从硬编码自身名改为模块位置推导
  - `anchor.py` pre-modify 扩展至 4 项全面检查（含 loop_dry_run、7 个关键模块）
  - subprocess 调用全部添加 `encoding="utf-8", errors="replace"`
  - patterns.md 锚点验证方式改为"实际验证方式（非声称）" + 新增模式 13 扫描器盲区
- **验证：** `python engine/anchor.py pre-modify-check` → **4/4 全过**；宪法可实现性审计全部可实现；`python engine/realizability.py` 持续监控宪法可实现性
- **已知剩余差距：**
  - `@realizability_gap` 平台声明：manifest 声明支持 Linux/macOS/Windows，但仅在 Windows 上实际运行过——架构理论可移植但未经实证
  - `@scanner_blindspot` 扫描器盲区：8 条规则不能覆盖所有问题类别——已知 8 类问题在外，宪法诚实承认而非假装全覆盖

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] v1.2 前沿框架蒸馏——十大架构缺口
- **环境：** Windows 11 / Python 3.13
- **现象：** v1.1蒸馏了6个框架，但2026年上半年前沿发展远超此范围
- **分析范围：** 18篇顶会论文 + 12个开源框架 + Claude Code内部架构 + 7个独立创作者框架
- **发现十大架构缺口：**
  - 🔴 P1 规则自生成(Gödel Agent) — rules.py只有5条硬编码规则，无法自动生成新规则
  - 🔴 P2 修复证据链(EVE-Agent) — 修复声称成功但无证据；缺evidence_span一级字段
  - 🔴 P3 独立验证器(ultragoal) — 验证和修复在同一进程中，非fresh-eyes
  - 🔴 P4 语义记忆(Engram) — insights是JSON glob扫描，无FTS5索引，无去重，无时态有效性
  - 🟡 P5 Hook集成(Claude Code) — 纯CLI工具，不利用Claude Code Hook系统自动触发
  - 🟡 P6 元生产力(HGM) — TAPO只追踪即时成功，不追踪下游级联效应
  - 🟡 P7 零起始Bootstrap(OpenSkill) — 需要patterns.md预存在，无法bootstrap新skill
  - 🟢 P8 并行候选(Yunjue) — loop是顺序的，不支持多候选并行评估
  - 🟢 P9 统一适应度(Karpathy) — 多指标无统一分数，无法爬山优化design_params
  - 🟢 P10 崩溃恢复(Claude Code) — 无resume-from-crash机制
- **修复：** 完整蒸馏报告 → references/distillation-report-2026-06-13.md
- **P1-P10全部实施完成** — 见下方记录
- **验证：** 全部通过。实时证明：4 个 skill 的免疫系统通过 bootstrap 自动创建。

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] v1.2 十大缺口一次性全部闭合
- **环境：** Windows 11 / Python 3.13 / bash
- **新增模块 (10个):**
  - P1 `engine/system2/rule_generator.py` (Gödel Agent 规则自生成, 310行)
  - P2 `engine/evidence.py` (EVE-Agent 证据链, 320行)
  - P4 `engine/memory_index.py` (Engram 语义记忆, 610行)
  - P5 `engine/hooks_bridge.py` (Claude Code Hook集成, 310行)
  - P6 `engine/memory.py::descendant_quality` (HGM 元生产力, 120行)
  - P7 `engine/bootstrap.py` (OpenSkill 零起始, 470行)
  - P8 `engine/parallel.py` (Yunjue 并行候选, 250行)
  - P9 `engine/hillclimb.py` (Karpathy 统一适应度, 280行)
  - P10 `engine/checkpoint.py` (Claude Code 崩溃恢复, 230行)
  - `engine/loop.py:P3 verify_execution_independent()` (Ultragoal 独立验证, 90行)
- **改造文件:** loop.py, memory.py, rules.py, anchor.py, realizability.py, scanner.py, constitution.md, manifest.json, skill.md (9个)
- **验证:**
  - anchor pre-modify-check → 4/4 passed
  - realizability audit → 8/8 passed (100% 宪法可通过)
  - loop dry-run → OK (GVU stable)
  - 全部 10 个新模块 --help → 正常
  - memory_index bootstrap → 18 docs FTS5 indexed
  - hillclimb fitness → 4.44 (优秀)
  - reflect cross-scan → 10 skills scanned, 4 shared patterns
  - bootstrap → 4 skills 自动免疫系统初始化 (genshin/hot-trend/storage/video)
- **代码规模:** 新增 ~4,000 行，总计 ~6,500 行
- **蒸馏框架:** 16 个前沿框架完整蒸馏
- **备注:** P1-P4 Phase 1, P5-P10 一次性补充, A/B/C 反熵机制 — 全部闭合

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 三大反熵机制 (A/B/C)
- **触发:** 架构评审发现五种未受控的熵增维度
- **实施内容:**
  - ✅ A. 规则老化器: `rule_generator.py:age_rules()` — 50轮0命中→休眠, 100轮→废弃移至 patterns-deprecated.md
  - ✅ B. 盲区发现器: `engine/blindspot.py` — 收集S2发现S1无法检测的模式, 3+次→自动生成候选规则, 收敛判断改为"零发现+盲区审计不过期"
  - ✅ C. 记忆合并器: `memory_index.py:auto_maintain_memory()` — 相似>85%自动合并, 20轮未被search→置信度衰减, 100轮→归档
- **改造文件:** rule_generator.py (+120行), memory_index.py (+120行), blindspot.py (新增260行), loop.py (收敛逻辑+反熵触发), manifest.json, anchor.py
- **验证:** anchor 4/4, realizability 8/8, A/B/C 三个命令全部正常运行

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 第一层蒸馏 (S1/S2/S3) — 质变级
- **S1 Socratic-SWE:** `engine/trace_distiller.py` (380行) — GDPO三维奖励替代布尔pass/fail, 四阶段验证门, 轨迹→技能蒸馏, adaptive probation, Agent Skill Registry
- **S2 TextGrad:** `evidence.py:+170行` — 文本梯度生成(失败模式+根因假说+修正建议), 梯度索引供rule_generator改进规则
- **S3 Mem²Evolve:** `memory_index.py:+80行` — 经验-资产双向链接(coevolution_links.db), 共进化统计(良性螺旋检测)
- **改造文件:** loop.py (S2梯度注入+S1蒸馏触发+S3共进化检查), evidence.py, anchor.py, realizability.py, scanner.py, patterns.py, manifest.json, skill.md
- **验证:** anchor 4/4, realizability 8/8, loop dry-run OK, GDPO demo OK, TextGrad输出正确, 共进化链接正常

<!-- @ttl version_added="1.2" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] v1.2 Phase 1 实施完成
- **环境：** Windows 11 / Python 3.13 / bash
- **实施内容：**
  - ✅ P1 规则自生成: `engine/system2/rule_generator.py` (Gödel Agent蒸馏) — 310行
  - ✅ P2 修复证据链: `engine/evidence.py` (EVE-Agent蒸馏) — 320行
  - ✅ P3 独立验证器: `loop.py:verify_execution_independent()` (Ultragoal蒸馏) — 90行
  - ✅ P4 语义记忆: `engine/memory_index.py` (Engram蒸馏) — 610行，SQLite FTS5 + LIKE fallback
  - ✅ 宪法修正: 第三/第五法则增加独立验证+证据链声明
  - ✅ 引擎集成: `rules.py` 合并生成规则，`memory.py` 新增2个event_types
  - ✅ manifest.json 更新至v1.2.0，skill.md更新架构描述
  - ✅ anchor.py key_modules扩展至10个模块
- **改造文件**: loop.py, memory.py, rules.py, anchor.py, constitution.md, manifest.json, skill.md, known-issues.md (8个)
- **新增文件**: rule_generator.py, evidence.py, memory_index.py, distillation-report-2026-06-13.md (4个)
- **验证**: anchor pre-modify-check 4/4 passed; realizability 8/8 passed; loop dry-run OK; memory_index bootstrap 18 docs
- **已知限制**: FTS5默认tokenizer不处理CJK分词——使用LIKE fallback保障中文搜索可用

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: add-utf8-header
- **文件:** engine\blindspot.py
- **动作:** added UTF-8 header

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\rules.py
- **动作:** 标注裸 except at line 110


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\rules.py
- **动作:** 标注裸 except at line 150


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\rules.py
- **动作:** 标注裸 except at line 202


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\scanner.py
- **动作:** 标注裸 except at line 109


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\scanner.py
- **动作:** 标注裸 except at line 115


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\scanner.py
- **动作:** 标注裸 except at line 122


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-bare-except
- **文件:** engine\system1\scanner.py
- **动作:** 标注裸 except at line 124

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\bootstrap.py
- **动作:** annotated except...pass across lines at 350


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\checkpoint.py
- **动作:** annotated except...pass across lines at 227


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\evidence.py
- **动作:** annotated except...pass across lines at 448


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\hooks_bridge.py
- **动作:** annotated except...pass across lines at 84


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\hooks_bridge.py
- **动作:** annotated except...pass across lines at 225


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 75


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 84


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 564


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 811


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 892


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 942


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 994


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1042


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1048


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1055


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1069


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1077


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1091


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1100


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1110


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1120


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1129


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\memory.py
- **动作:** annotated except...pass across lines at 562


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\memory.py
- **动作:** annotated except...pass across lines at 610


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\memory.py
- **动作:** annotated except...pass across lines at 680


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\parallel.py
- **动作:** annotated except...pass across lines at 200


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\redteam.py
- **动作:** annotated except...pass across lines at 160


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\redteam.py
- **动作:** annotated except...pass across lines at 191


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\regression_guard.py
- **动作:** annotated except...pass across lines at 131


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\skill_composer.py
- **动作:** annotated except...pass across lines at 159


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\trace_distiller.py
- **动作:** annotated except...pass across lines at 421

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\system2\code_generator.py
- **动作:** annotated except...pass across lines at 142


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\system2\rule_generator.py
- **动作:** annotated except...pass across lines at 430

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 264


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-gepa-failed-tag-except-pass
- **文件:** engine\system1\rules.py
- **动作:** added GEPA compile-check guard to fix_except_pass (both Case 1 and Case 2)

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 1307

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: add-utf8-header
- **文件:** scripts\codegen_hook.py
- **动作:** added UTF-8 header

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 40


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 58

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 46

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: add-utf8-header
- **文件:** scripts\codegen_hook.py
- **动作:** added UTF-8 header


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 54


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 233


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 247


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** scripts\observer_daemon.py
- **动作:** annotated except...pass across lines at 394

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: tag-except-pass
- **文件:** engine\loop.py
- **动作:** annotated except...pass across lines at 948

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 24


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-hardcoded-numbers
- **文件:** constitution.md
- **动作:** replaced '10轮' with '多轮'

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 24


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-hardcoded-numbers
- **文件:** constitution.md
- **动作:** replaced '20轮' with '多轮'

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 25

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 239


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 240


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 241


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 242


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 243


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 244


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 245


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 26

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 246


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 247


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 248


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 249


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 250


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 251


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 252


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 253


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 254


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 255


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 27

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/ask_gate_confidence_gap.jsonl
- **动作:** marked dead metric on line 256


<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28

<!-- @ttl version_added="1.0" last_referenced_iso="2026-06-13" reference_count="0" -->
### [2026-06-13] 自动修复: fix-metric-dead-value
- **文件:** data/metrics/false_convergence_rate.jsonl
- **动作:** marked dead metric on line 28
