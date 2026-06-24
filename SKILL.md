---
name: mycos-eval
description: 自动跑完麦可思 mycos 教学评价（评教）问卷。识别问卷结构、规避反作弊（满分会被拒、评价过高需写原因等）、为不同问卷场景生成合适的简短评语并提交。**触发**：用户说"评教""教学评价""麦可思""mycos""跑评教"，或表达不想手动填评教/学生评管。第一次需要扫码/账密登录一次保存会话，之后可一键无人值守。
---

# 教学评价自动化（麦可思 mycos）

## 这个 skill 的核心承诺

帮用户**完全无人值守**地跑完一份或多份麦可思评教问卷：

1. 检查登录会话是否还有效
2. 现场决定一句**短小、正面、无建议**的中文评语
3. 启动 Python 脚本 headless 遍历所有未完成评教并提交
4. 报告结果

**重要原则：除了第一次登录（扫码/SSO 没法绕开），任何后续调用都不应让用户做手动操作**——不要让用户粘脚本、不要让用户开 F12、不要让用户开浏览器。

## skill 目录与命令

skill 位于 Claude Code 的标准 skill 路径下（通常是 `~/.claude/skills/mycos-eval/`）。
此 SKILL.md 旁边的两个文件：

- `eval.py`：Python + Playwright 自动化脚本
- `requirements.txt`：依赖清单（一个 playwright）

**执行 Python 命令时必须用 skill 同目录下 `.venv/bin/python`**，例如（设 skill 目录为 $SKILL_DIR）：

```
$SKILL_DIR/.venv/bin/python $SKILL_DIR/eval.py <子命令> [选项]
```

如果 `.venv` 不存在，让用户先按 README 创建：
```
cd $SKILL_DIR
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 调用时执行步骤

### Step 1：检查登录会话

```
$SKILL_DIR/.venv/bin/python $SKILL_DIR/eval.py check
```

stdout 输出 `valid` / `expired` / `none` / `error` 之一。

- `valid`：跳到 Step 3
- `expired` / `none`：进 Step 2
- `error`：读 `$SKILL_DIR/.state/last_run.log` 末尾 30 行，把关键报错贴给用户

### Step 2：登录（仅首次或会话过期）

告诉用户："会话过期了，需要登录一次。我现在打开浏览器，你登录后脚本会自动检测保存。"

后台运行（让 Chrome 窗口出现）：
```
$SKILL_DIR/.venv/bin/python $SKILL_DIR/eval.py login
```

脚本会自动检测 URL 跳到 app 路由后保存 `.state/storage_state.json`。如果自动检测不灵，可让 Claude 创建哨兵文件 `touch $SKILL_DIR/.state/proceed` 强制保存。

### Step 3：根据当前问卷类型，决定一句短文本

**不要预生成 JSON 评语池。** 用户已明确要求每次填写时由 agent 现场决定，争取**无不良影响、无建议、真善美、尽量简短**。

跑前先快速判断用户当前要填的问卷类型（可问用户、看 home 页面、或从 `last_run.log` 推测），按以下场景**写一个 8-15 字的短句**作为 `--text` 参数：

| 问卷类型 | 主文本示例 | 备注 |
|---|---|---|
| 教师评价（过程评价 / 期中 / 期末 / 教学评价） | "老师讲得好，学到了很多。" | 围绕"夸老师 + 学习收获" |
| 学生评管 / 管理类 / 学生事务 / 后勤 | "无意见。" 或 "管理工作到位。" | **绝不要写"老师"** |
| 不确定 / 通用 | "无意见。" | 安全兜底 |

不要生成多条，就一句。脚本把所有非"建议/意见"类文本框都填这一句。"建议/意见/想法/希望"等开放题脚本用正则自动填"无"。

### Step 4：跑

```
$SKILL_DIR/.venv/bin/python $SKILL_DIR/eval.py run --text "<上一步想好的短句>"
```

**评分策略（默认行为，用户偏好）**：脚本默认"尽量给最高档；只有触发'评价较高'
弹窗时，才把一道题从最高降到次高，逐档增加直到能无弹窗提交"——即用最少的降档换
"无弹窗 + 尽量高分"。找到的阈值会跨表单复用（同一问卷模板阈值一致），所以只有第一份
表单会多几次降档重试，后续表单直接从已学到的档位起步。**默认不要再加 `--moderate`**。

如果用户带参数：
- `--review` / `--dry-run` → 加 `--dry-run`（填好不提交）
- `--headed` → 加 `--headed`（显示浏览器，调试）
- 用户想"起步就低一点、少几次重试" → 加 `--start-downgrade N`（N 为起始降档数）；
  `--moderate` 等价于 `--start-downgrade 3`

正常情况脚本 headless 跑完，最后一行 stdout 是 JSON：`{"done": N, "skipped": M}`。

### Step 5：报告结果

给用户一句话总结：
- "完成 N 条评教，跳过 M 条"
- 如果 `skipped > 0`，提示截图在 `$SKILL_DIR/.state/screenshots/` 可查
- 如果 `done == 0` 且 `skipped == 0`：可能没有未完成评教

退出码非 0：
- `4` → 会话过期，建议用户重新调用 skill（Step 1 自动走 login）
- 其他 → 读 `.state/last_run.log` 末尾贴给用户

## 主文本风格规则（写 `--text` 那一句话时遵守）

**核心原则（用户明确要求）：争取无不良影响，无建议，真善美，尽量简短。**

- **长度**：8-15 字，最多 20 字
- **正面**：纯正面/中性，不掺任何负面、批评、建议、改进意味
- **不要建议**：连"如果能...就更好"也不写。建议题脚本自动填"无"
- **场景适配**：
  - 教师评价：围绕"老师 + 学到/收获"
  - 学生评管/管理类：**绝不写"老师"**，用"无意见。""管理工作到位。"
  - 不确定：默认"无意见。"
- **可重复性**：同一句会被脚本重复用在该问卷所有非建议题里，所以选一句"用在哪都不违和"的

## 已知问卷类型（mycos 通用）

mycos 平台同一个学生可能同时收到几种问卷：

- **过程评价 / 期中评价**（type=5, typeCode=Middle）：home → 问卷卡片 → 课程列表（每门课一卡片，授课教师 X/Y）→ 各门课的 form → 提交后弹"提交成功" modal + "下一门课程"按钮 → ... → 最后一门提交后整页跳"评价完成"
- **期末评价**（type=1, typeCode=Final）：结构同上
- **学生评管**（type=7）：home → 问卷卡片 → **直接 form**（没有课程列表中间层）→ 提交后整页"评价完成"
- **多教师课程**：一门课挂多位老师（如"汽车设计 / 耿国庆,张兴龙"）时，课程页顶部有教师子 Tab，需逐个老师评。某老师评完后其 Tab 出现对勾图标、表单按钮变"已提交"。

skill 的状态机 `home / survey-list / form / teacher-tabs / success / survey-complete / reason-modal / plain-modal` 同时兜住这几种结构。其中 `teacher-tabs` 在当前老师已评完、但子 Tab 里还有未评老师时触发，自动切到该老师后页面即变回 `form`。

## 已知反作弊行为

1. **不允许全选满分 / 评价较高需写原因**：整体打分过高时，提交会弹"您对该教师的评价较高，请填写原因"模态框（antd-mobile 的 `am-modal`，确认/取消键是 `<a class="am-modal-button">` 不是 `<button>`）。脚本默认采用**自适应降档**应对：先全给最高档，弹窗就取消、把一道题降到次高、返回重交，逐档加直到无弹窗（详见 Step 4 评分策略）。降到题数上限仍弹窗、或"取消"失效时，兜底用 `--text` 那句作原因确认提交。
2. **首页不自动隐藏已完成的问卷卡片**：完成一份问卷后，首页那张卡片仍可见，文字也不带"已完成"。脚本用 run-local blacklist：进 survey-list 发现没"进行中"课程 → 把 last_home_pick 加入黑名单，下次首页选卡时跳过。
3. **首页不自动隐藏已完成的问卷卡片**：完成一份问卷后，首页那张卡片仍可见，文字也不带"已完成"。脚本用 run-local blacklist：进 survey-list 发现没"进行中"课程 → 把 last_home_pick 加入黑名单，下次首页选卡时跳过。

## 错误处理 Cheatsheet

| 现象 | 怎么办 |
|---|---|
| `check` 返回 `expired` | 自动走 Step 2 重新登录 |
| `run` 退出码 4 | 同上 |
| `run` 完成但 done=0 skipped=0 | 没有未完成评教，告诉用户 |
| `run` skipped 很多 | 读 `.state/screenshots/empty-form-*.png` / `unknown-*.png`，可能是页面结构变了，需要调整 `eval.py` 里的选择器 |
| Chrome ERR_TIMED_OUT 在 SSO 页 | 学校 SSO 走 IPv4 的兜底失效。`dig +short A <SSO 域名>` 看 IPv4，更新 `--resolver "MAP <pattern> <ip>"` |
| 用户报"提交一直卡在 success 状态" | success modal 里的"下一门课程"找不到，看 `.state/screenshots/success-*.png` 调整 HANDLE_SUCCESS_JS 选择器 |

## 不要做的事

- ❌ **不要**降级成"给用户一份脚本让他粘贴"——用户已明确不想手动
- ❌ **不要**改用 Tampermonkey、bookmarklet、控制台粘贴等方案
- ❌ **不要**直接调 mycos 的内部 API（请求体加密，且没有撤销提交端点）
- ❌ **不要**每次都重新让用户登录（除非 check 返回 expired/none）
- ❌ **不要**预生成多条评语 JSON 池——按 Step 3 现场决定一句即可
- ❌ **不要**把"老师讲得好"用在学生评管/学校管理类问卷上
