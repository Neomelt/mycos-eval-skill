# mycos-eval — agent instructions

> 这份文件是给 AI 编码助手（Codex CLI / Cursor / Cline / Aider / Gemini CLI /
> Continue / 任何能调 shell 的 agent）看的。Claude Code 用同目录的 `SKILL.md`
> （加了 frontmatter 用于自动激活），内容基本相同。

## 你是什么

你是一个能调 shell 命令的 AI 助手。仓库提供一个 Python + Playwright 工具
`eval.py`，自动遍历麦可思（mycos）评教问卷并提交。**用户调用你的目标是
完全无人值守地把评教跑完**——不要让用户手动粘脚本、开 F12、或自己开浏览器。

## 触发场景

当用户说出下列内容时，运行本工具：

- "跑评教"、"做教学评价"、"麦可思"、"mycos"
- "学生评管"、"过程评价"、"期中评价"、"期末评价"
- 表达不想手动填学校的评教问卷

## 工具位置

仓库根目录假设是 `$REPO`（用户 clone 到的位置，常见为
`~/.claude/skills/mycos-eval/` 或 `~/code/mycos-eval-skill/`）。

需要的执行环境：

- `$REPO/.venv/bin/python`：venv 内的 Python（首次需要按 README 安装）
- `$REPO/eval.py`：主脚本

如果 `.venv` 不存在，让用户先：
```bash
cd $REPO
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 操作流程

### Step 1：检查会话

```bash
$REPO/.venv/bin/python $REPO/eval.py check
```

stdout 输出 `valid` / `expired` / `none` / `error` 之一。

- `valid` → 直接进 Step 3
- `expired` / `none` → 进 Step 2
- `error` → 读 `$REPO/.state/last_run.log` 末尾贴给用户

### Step 2：登录（仅首次或会话过期）

提示用户："会话过期了，需要登录一次。我现在打开浏览器，你登录后脚本会自动检测保存。"

后台运行：
```bash
$REPO/.venv/bin/python $REPO/eval.py login
```

脚本会自动检测 URL 跳到 app 路由后保存 `.state/storage_state.json`。10 分钟超时。

### Step 3：决定一句短文本

**不要预生成 JSON 评语池。** 现场决定一句：

| 问卷类型 | 主文本示例 | 备注 |
|---|---|---|
| 教师评价（过程评价 / 期中 / 期末 / 教学评价） | `老师讲得好，学到了很多。` | 围绕"夸老师 + 学习收获" |
| 学生评管 / 管理类 / 后勤 | `无意见。` 或 `管理工作到位。` | **不要写"老师"** |
| 不确定 / 通用 | `无意见。` | 安全兜底 |

**风格约束**（用户明确要求）：

- 长度 8-15 字，最多 20 字
- 纯正面/中性，不带任何负面、批评、改进建议口气
- "建议/意见/想法/希望"等开放题脚本会自动填"无"，你不必处理

### Step 4：跑

```bash
$REPO/.venv/bin/python $REPO/eval.py run --text "<上一步那一句>"
```

可选参数：
- `--dry-run`：填好不提交
- `--headed`：显示浏览器（调试用）
- `--moderate`：混入更多次高档评分
- `--max N`：最多处理 N 条

正常结束 stdout 最后一行是 JSON `{"done": N, "skipped": M}`。

### Step 5：报告结果

一句话总结：done=N、skipped=M。skipped > 0 时提示截图在
`$REPO/.state/screenshots/` 可查。退出码非 0 时读
`.state/last_run.log` 末尾贴关键报错。

## 已知反作弊（脚本已处理，不要绕开）

1. **不允许全选满分**：脚本对每份表单强制随机 3 道选次高档
2. **评价较高需写原因**：脚本检测到此 modal 自动用 `--text` 那句填理由
3. **首页不隐藏已完成卡片**：脚本用 run-local blacklist 跳过

## 适配其他学校

默认配置是江苏大学（UJS）：

- 入口 URL：`https://jpv2-2.mycospxk.com/wx/ver3.41.0/index.html`
- IPv4 强制：`MAP *.ujs.edu.cn 218.3.94.83`

其他学校：
```bash
# 换入口
$REPO/.venv/bin/python $REPO/eval.py run \
    --url "https://<your-school-mycos>/wx/ver3.41.0/index.html" \
    --text "..."

# 关掉 IPv4 强制（多数学校不需要）
$REPO/.venv/bin/python $REPO/eval.py --resolver "" run --text "..."

# 换成你学校 SSO 的 IP
$REPO/.venv/bin/python $REPO/eval.py \
    --resolver "MAP *.your-school.edu.cn 1.2.3.4" \
    run --text "..."
```

## 别做的事

- ❌ 不要降级成"我给你脚本你自己粘到浏览器跑"
- ❌ 不要直接调 mycos 内部 API（请求体加密 + 没有撤销端点）
- ❌ 不要每次都让用户重新登录（除非 `check` 返回 expired/none）
- ❌ 不要预生成多条评语 JSON 池
- ❌ 不要把"老师讲得好"用在学生评管/学校管理类问卷上
- ❌ 不要尝试"已提交后撤回重做"——服务端不允许，强行操作有学籍风险
