# PAT-Agent-RT: Real-Time System Extension

基于 [PAT-Agent](https://ieeexplore.ieee.org/document/11334568)（ASE 2025）框架扩展的**实时系统（Real-Time System, RTS）自动化形式化验证**模块。在原框架仅支持离散 CSP 验证的基础上，新增对 PAT RTS 模块五序算子的自动建模与验证修复支持。

---

## 目录结构

```
PAT-Agent-master/
├── Automated_Pipelines/
│   ├── Full_Pipeline/              # 全流水线（规划 + 修复）
│   │   ├── pipeline.py             # ★ 主流水线（CSP/RTS 自适应）
│   │   ├── pipeline_single.py      # 单 case 调试用流水线
│   │   ├── run_experiments.py      # ★ 批量实验运行器（RQ1/RQ2）
│   │   ├── syntax-dataset.json     # CSP 语法知识库（原有）
│   │   ├── syntax-dataset-rt.json  # ★ RTS 语法知识库（新增）
│   │   ├── database-rag-claude.json# CSP RAG 示例库（原有）
│   │   ├── database-rag-rts.json   # ★ RTS RAG 示例库（新增，9 个已验证示例）
│   │   ├── database-algorithm.json # 验证通过的模型存档
│   │   ├── eval_time.ipynb         # 运行时统计分析
│   │   ├── generated_code/         # Full 配置生成代码
│   │   ├── generated_code_norepair/# No Repair 配置生成代码
│   │   ├── history/                # LLM 交互历史
│   │   └── run_time_record/        # 各阶段耗时记录
│   ├── No_Planning/
│   │   ├── pipeline_a.py           # 无规划 CSP 流水线（原有）
│   │   ├── pipeline_rt_no_planning.py # ★ 无规划 RTS 流水线（新增）
│   │   ├── generated_code/         # No Planning 配置生成代码
│   │   ├── generated_code_both/    # Both Off 配置生成代码
│   │   ├── history/
│   │   └── run_time_record/
│   ├── requirements.txt
│   └── README.md
├── Datasets/
│   ├── PAT-RT.json                 # ★ RTS 数据集（16 个案例）
│   ├── PAT.json                    # CSP 数据集（原有 40 案例）
│   ├── A4F.json
│   └── UCS.json
├── Interface/                      # Web 交互界面
│   ├── server.py
│   └── templates/
├── PAT.Console/                    # PAT 3.5.1 模型检查器
└── PAT-Agent_Autoformalization_for_Model_Checking.pdf
```

★ 标记为本 RTS 扩展新增或重大修改的文件。

---

## 环境配置

### 1. 创建 Conda 环境

```bash
conda create -n PAT-agent python=3.9 -y
conda activate PAT-agent
```

### 2. 安装依赖

```bash
cd Automated_Pipelines
pip install -r requirements.txt
```

依赖包：`openai`、`scikit-learn`、`flask`、`flask-cors`、`sentence-transformers`

### 3. 设置 API Key

```powershell
# PowerShell
$env:DEEPSEEK_API_KEY = 'sk-your-deepseek-api-key'
```

### 4. 修改路径配置

在 `pipeline.py`、`run_experiments.py` 等文件中，将以下路径替换为你的实际路径：

- `root_path`：项目根目录的绝对路径
- `PAT_EXE`：PAT 3.5.1 Console 可执行文件路径（默认：`C:\Program Files\Process Analysis Toolkit\Process Analysis Toolkit 3.5.1\PAT3.Console.exe`）

---

## 快速开始

### 运行全流水线（单个 Case）

```bash
cd Automated_Pipelines/Full_Pipeline
python pipeline_single.py
```

默认运行 `Datasets/PAT-RT.json` 中的第一个案例（`heartbeat_monitor`）。可在 `pipeline_single.py` 中修改 `model_index` 变量切换案例。

### 批量运行实验

```bash
cd Automated_Pipelines/Full_Pipeline
python run_experiments.py
```

该脚本自动执行四组消融配置：
- **Full**：规划 + 修复（全流水线）
- **No Repair**：仅规划，无修复
- **No Planning**：仅修复，无规划
- **Both Off**：无规划，无修复（LLM 直接生成）

每组配置的结果保存到对应 `generated_code*/` 目录。

---

## RTS 扩展详解

### 1. 目标模块自适应架构

流水线通过数据集中的 `targetModule` 字段自动选择验证目标：

| targetModule | PAT 引擎 | 文件扩展名 | 知识库 | RAG 库 |
|-------------|----------|-----------|--------|--------|
| `csp` | `-csp` | `.csp` | `syntax-dataset.json` | `database-rag-claude.json` |
| `rts` | `-rts` | `.rts` | `syntax-dataset-rt.json` | `database-rag-rts.json` |

核心实现见 `pipeline.py` 中的 `_get_target_module()`、`_get_module_file_extension()`、`_get_module_resources()` 三个函数。

### 2. RTS 语法知识库

`syntax-dataset-rt.json` 采用**"参考卡片 + 陷阱规则"双层结构**：

**参考卡片层**（10 个顶层键）：
| 键 | 内容 |
|---|------|
| `general_info` | RTS 全局约束（仅支持 `\|\|` 和 `\|\|\|`、不支持字母并行、bounded Wait 崩溃警告） |
| `wait` | `Wait[t];` 语法模板 + 必须使用分号的关键规则 |
| `timeout` | `P timeout[t] Q` 超时切换语义 |
| `deadline` | `P deadline[t]` 截止期约束 |
| `within` | `P within[t]` 首事件时限 |
| `interrupt` | `P interrupt[t] Q` 时间中断 |
| `ifb` | 阻塞条件（RTS 中替代 `ifa` 用于时序进程） |
| `urgent` | `event ->> Process` 紧急事件 |
| `assertions` | RTS 支持的三类断言格式（注：LTL `X`/next 算子不支持） |

**陷阱规则层**（`pitfalls_rules`，12 条规则）：
- 规则 1-6：CSP/RTS 通用规则（分号、数组初始化、枚举、守卫条件等）
- 规则 7.1-7.6：**RTS 特有陷阱**，包括：
  - 时序进程不能出现在 `[guard]P` 或 `ifa{}` 内部（需用 `ifb`）
  - 数据操作 `{}` 与时序算子之间需有 `tick` 事件分隔
  - `Wait[t]` 必须用 `;` 而非 `->` 连接
  - RTS 保留字不可作事件标识符
  - `#define` 中 `->` 被 PAT 解析为过程前缀
  - bounded `Wait[t1,t2]` 和 `within[t1,t2]` 触发运行时崩溃

### 3. 双轨反例解析

验证阶段根据目标模块自动选择反例处理策略：

- **CSP 轨道**（`_process_mismatch_traces_csp`）：反例被解析为线性事件序列，修复指令基于"位置越靠后、频率越高则嫌疑越大"的启发式策略生成。
- **RTS 轨道**（`_process_mismatch_traces_rts`）：额外识别三类 RTS 特有失败：
  1. **解析/语法错误**：提取 PAT 精确错误信息（如 `Invalid Symbol 'timeout'`）并注入 RTS 陷阱清单
  2. **死锁违反**：解析含 `tick`/`tock` 事件的时间轴反例
  3. **断言违反**：提取含时间推进事件的反例轨迹

### 4. 增强错误反馈

诊断策略从原始 PAT-Agent 的 4 种拓展至 6 种：
- 死锁违反、可达性缺失、LTL 违反（原有 3 种）
- **解析错误定位**（新增）：将 PAT Parsing Error 的结构化信息注入修复提示词
- **时间约束反例解释**（新增）：将 `tick`/`tock` 时间轴信息编码为修复指令
- 编译失败（原有 1 种，现细化为有/无 PAT 错误信息两种）

### 5. RTS 流水线的特殊处理

在 RTS 模式下，流水线在动作提取阶段采用**简化路径**：

- **CSP 模式**：常量变量提取 → 独立动作提取（LLM 分析每个进程的行为和状态转移）→ NL 指令整合
- **RTS 模式**：常量变量提取 → **跳过独立动作提取** → 直接将系统描述和时序约束摘要拼入 NL 指令

原因：PAT RTS 模块的进程交互以事件同步为主，跳过独立的 JSON 动作提取阶段可避免级联的 JSON 解析错误，同时引导代码生成 LLM 关注时序算子的正确使用。

---

## RTS 数据集

`Datasets/PAT-RT.json` 包含 16 个实时系统案例，覆盖五种时序算子和三类断言类型：

| 复杂度 | 案例 | 进程数 | 主要时序算子 | 断言 |
|--------|------|--------|-------------|------|
| 低 | light_blinker | 1 | Wait/timeout | deadlockfree |
| 低 | traffic_light | 1 | Wait/timeout | deadlockfree |
| 低 | pedestrian_light | 1 | Wait | deadlockfree + LTL |
| 中 | heartbeat_monitor | 2 | Wait + timeout | deadlockfree + reaches |
| 中 | coffee_brew | 2 | within + timeout | deadlockfree + reaches |
| 中 | atm_timeout | 2 | timeout | deadlockfree + reaches |
| 中 | elevator_door | 2 | interrupt | deadlockfree + reaches |
| 中 | parking_meter | 2 | timeout | deadlockfree + reaches |
| 中 | workstation | 2 | deadline | deadlockfree + reaches |
| 中 | railway_control | 2 | within + deadline | deadlockfree + LTL |
| 中 | pacemaker | 2 | Wait + timeout | deadlockfree + reaches |
| 高 | reactor_shutdown | 2 | timeout ⊂ deadline | deadlockfree + reaches |
| 高 | csma2 | 2 | Wait | deadlockfree + LTL |
| 高 | fddi | 3 | Wait + timeout | deadlockfree + reaches |
| 高 | lift_system | 3 | Wait | deadlockfree + reaches |
| 高 | fischer_protocol | 4 | Wait | deadlockfree + LTL×2 |

数据集格式示例：

```json
{
  "modelName": "heartbeat_monitor",
  "modelType": "timed",
  "targetModule": "rts",
  "modelDesc": "A sender emits heartbeat periodically...",
  "interactionMode": "none",
  "subsystemCount": 2,
  "timeConstraints": [
    {"description": "heartbeat interval is 2 time units", "type": "periodic", "bound": 2}
  ],
  "subsystems": [
    {"name": "Sender", "description": "Sends heartbeat signal every 2 time units."}
  ],
  "assertions": [
    {"assertionType": "deadlock-free", "assertionTruth": "Valid"},
    {"assertionType": "reachability", "stateName": "alarm_state", "assertionTruth": "Valid"}
  ]
}
```

---

## 消融实验

### 四组配置

| 配置 | 规划 LLM | 修复循环 | 运行脚本 | 代码输出目录 |
|------|----------|----------|----------|-------------|
| Full | ✅ | ✅ | `Full_Pipeline/pipeline.py` | `generated_code/` |
| No Repair | ✅ | ❌ | `Full_Pipeline/pipeline.py`（关闭修复） | `generated_code_norepair/` |
| No Planning | ❌ | ✅ | `No_Planning/pipeline_rt_no_planning.py` | `No_Planning/generated_code/` |
| Both Off | ❌ | ❌ | `No_Planning/pipeline_rt_no_planning.py`（关闭修复） | `No_Planning/generated_code_both/` |

### 评估指标

- **CSR**（Compilation Success Rate）：编译成功率，代码能被 PAT 解析的比例
- **FPR**（Full Pass Rate）：全断言通过率，所有断言验证正确的案例比例
- **APR**（Assertion Pass Rate）：逐断言通过率，所有断言中验证正确的比例

### 运行实验

```bash
cd Automated_Pipelines/Full_Pipeline
python run_experiments.py
```
---