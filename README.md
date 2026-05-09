# mycos-eval-skill

A [Claude Code](https://claude.com/claude-code) skill that automates filling and
submitting **MyCOS (麦可思)** student-evaluation questionnaires used by many
Chinese universities. Tested against the UJS (Jiangsu University) instance at
`jpv2-2.mycospxk.com`; the underlying logic should work on any school's mycos
deployment with at most a URL/IP override.

> 学校的教学评价/学生评管问卷大多是凑数任务，这个 skill 用 Playwright headless
> 把它跑完。第一次需要扫码/账密登录一次保存会话，之后调用 Claude Code 时说
> "跑评教"就完事。

**这是个学习/自用工具，不要拿去给别人作弊或刷爆服务器。** 学校的"诚信作答"约束
你自己拿捏。仓库不接受任何用于规避评分的 PR。

---

## 它能做什么

- 自动遍历 home 页所有未完成问卷
- 状态机识别 home / 课程列表 / 表单页 / 提交成功 modal / 评价较高原因 modal /
  评价完成总结页 等多种页面
- 单选题默认选最高档；自动随机 3 道选次高档**避开"全满分被拒"**的反作弊
- 文本框：根据题干关键词自动区分
  - "建议/意见/想法/希望" 类 → 一律填"无"
  - 其他 → 填用户调用 skill 时由 Claude 现场决定的那一句短文本
- 检测到"评价较高，请填写原因"模态框时自动填理由
- 完成一份问卷后自动点"下一门课程"或返回首页找下一份
- 完成的问卷卡片在首页加 run-local blacklist 防死循环

不能做：

- 撤销已提交的评价（mycos 服务端没暴露这个接口）
- 在没有桌面图形环境的机器上跑（首次登录需要弹出 Chrome 窗口）
- 处理强校验的微信扫码登录场景（需要 SSO 或账密能在桌面浏览器完成）

## 安装

### 前置条件

需要这些东西，没有的先装：

| 要的东西 | 检查命令 | 没装的话 |
|---|---|---|
| **桌面 Linux 或 macOS** | `uname -a` | Windows 没测过，理论上能跑但首次登录的浏览器弹窗会有兼容性问题 |
| **Python ≥ 3.10** | `python3 --version` | Ubuntu 22.04+/macOS 12+ 自带；旧系统升级 Python |
| **python3-venv 模块**（Linux 限定） | `python3 -m venv --help` 不报错 | Ubuntu/Debian: `sudo apt install python3-venv` |
| **Google Chrome** | `google-chrome --version` 或 `which google-chrome-stable` | https://www.google.com/chrome/ 直接装；本工具复用系统 Chrome，不另下载 |
| **git** | `git --version` | Ubuntu: `sudo apt install git`；macOS: `xcode-select --install` |
| **桌面图形会话** | `echo $DISPLAY` 非空 | 远程 ssh 无 X11 转发会卡在首次登录步骤；要在物理屏前/带桌面环境的机器上跑 |

### 如果你不熟悉 Python 虚拟环境

> Python 的 venv 就是给项目建一个**独立的 Python 包池**，
> 装的依赖只在那个目录里生效，不污染系统 Python，不和别的项目打架。
> 这个仓库需要 `playwright` 包，装到 venv 里最干净——
> 卸的时候删除 `.venv` 目录就行，不留痕迹。
>
> 别的方案（pipx / uv / conda 等）也行，但下面这一套用 venv 是最简单、
> 不用装额外工具的。

### 安装步骤（保姆版）

**第 1 步：clone 仓库到 Claude Code 的 skill 目录**

```bash
# 如果用 Claude Code，clone 到 skills 目录最方便（自动激活）
git clone https://github.com/Neomelt/mycos-eval-skill.git \
    ~/.claude/skills/mycos-eval

cd ~/.claude/skills/mycos-eval
```

> ⚠️ 仓库名是 `mycos-eval-skill`，但 clone 时**目录名要改成 `mycos-eval`**
> （和 `SKILL.md` 里 `name: mycos-eval` 对齐，Claude Code 才认）。
>
> 如果你不用 Claude Code，可以 clone 到任意位置，例如：
> `git clone https://github.com/Neomelt/mycos-eval-skill.git ~/code/mycos-eval-skill`

**第 2 步：建虚拟环境**

```bash
python3 -m venv .venv
```

跑完目录里会多一个 `.venv/`。这是这个项目专用的 Python 沙箱，已经在
`.gitignore` 里了，不会被 commit。

**第 3 步：在虚拟环境里装依赖**

```bash
.venv/bin/pip install -r requirements.txt
```

只装一个 `playwright` 包，不到 30 秒。装完会出现 `playwright` 这个命令位于
`.venv/bin/playwright`。

**第 4 步：验证装好了**

```bash
.venv/bin/python eval.py --help
```

应当看到 `usage: eval.py [-h] [--resolver RESOLVER] {login,check,run} ...`
之类的帮助。看到就 OK，可以直接跳到[使用](#使用)。

> 注意：本工具**不下载**额外的 Chromium（很多 Playwright 教程会让你跑
> `playwright install chromium`，那一步会下 130MB），而是复用你系统里
> 已经装好的 Chrome。所以省掉了那一步。

### 完整流水（一段贴板）

```bash
# Linux/macOS 通用，假设 git/python3/Chrome 都已就绪
git clone https://github.com/Neomelt/mycos-eval-skill.git \
    ~/.claude/skills/mycos-eval
cd ~/.claude/skills/mycos-eval
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python eval.py --help    # 验证
```

### 常见安装报错

<details>
<summary><strong>The virtual environment was not created successfully because ensurepip is not available</strong></summary>

Ubuntu/Debian 默认 Python 没带 venv 模块，跑：
```bash
sudo apt install python3-venv python3-pip
```
然后重试 `python3 -m venv .venv`。
</details>

<details>
<summary><strong>error: externally-managed-environment</strong>（Ubuntu 24.04 等新系统）</summary>

这是 PEP 668 限制系统 pip 的报错。**只要你已经在 venv 里**（用
`.venv/bin/pip` 而不是裸的 `pip`）就不会触发。如果触发了，说明你跳过了
venv 那一步，回去补上。
</details>

<details>
<summary><strong>BrowserType.launch: Executable doesn't exist at .../chrome-linux/chrome</strong></summary>

Playwright 在找它自己下载的 chromium 但没下载。本工具用的是系统 Chrome
（`channel="chrome"`），不该走到这条路径。如果你看到这个报错，说明
`google-chrome` 命令在 PATH 里找不到。`which google-chrome` 检查；
没装就装一个。
</details>

<details>
<summary><strong>首次运行 login 时 Chrome 弹不出来</strong></summary>

- 检查 `echo $DISPLAY` 非空（必须有桌面会话）
- 远程 ssh 用户：要么用 X11 转发（`ssh -X`）+本地 X server，要么换到物理屏前操作
- macOS：第一次启动 Chrome 可能需要在 System Settings → Privacy 里允许
</details>

## 使用

### Claude Code（最丝滑）

clone 到 `~/.claude/skills/mycos-eval/`，Claude Code 会自动读 `SKILL.md` 注册
skill。在会话里随便说一句："跑一下评教" / "帮我做教学评价" / "搞一下麦可思"
即触发。

第一次会让你登录学校 SSO 一次（Chrome 窗口会自动弹出），之后会话复用直到
cookie 过期。

### 其他 AI agent（Codex CLI / Cursor / Cline / Aider / Gemini CLI 等）

仓库根有一份 [`AGENTS.md`](AGENTS.md)，是给非-Claude agent 看的指令书，内容
跟 SKILL.md 基本一样但去掉了 Claude 专属的 frontmatter。

**Codex CLI**：clone 到任意位置，在仓库目录里跑 `codex`，让它读
`AGENTS.md`（Codex 默认会自动读取项目根的 AGENTS.md）。

**Cursor / Cline**：clone 到任意位置，把仓库当工作区打开，Cursor 会读
`AGENTS.md`（也兼容 `.cursorrules`，需要的话可以软链一下：
`ln -s AGENTS.md .cursorrules`）。

**Aider**：
```bash
aider --read AGENTS.md
```
然后让 aider 跑工具。

**Gemini CLI**：把 AGENTS.md 软链或重命名为 `GEMINI.md`：
```bash
ln -s AGENTS.md GEMINI.md
```
Gemini CLI 默认读取项目根的 `GEMINI.md` 作为上下文。

**DeepSeek / GLM / Qwen / 其他直调 API 的方案**：把 AGENTS.md 内容作为
system prompt 注入。底下的 shell 工具调用一致。

**任何能跑 shell 命令的 agent**：核心命令都是
`/path/to/repo/.venv/bin/python /path/to/repo/eval.py {check,login,run}`，
不依赖任何 agent 特性。

## 命令行直接用（不通过 Claude Code）

```bash
# 首次登录
.venv/bin/python eval.py login

# 检查会话
.venv/bin/python eval.py check    # 输出 valid / expired / none / error

# 跑（把"老师讲得好，学到了很多。"作为非建议题的统一文本）
.venv/bin/python eval.py run --text "老师讲得好，学到了很多。"

# 调试：显示浏览器
.venv/bin/python eval.py run --text "..." --headed

# 填好但不提交（看一眼）
.venv/bin/python eval.py run --text "..." --dry-run

# 学校反作弊更严时混入更多次高档
.venv/bin/python eval.py run --text "..." --moderate
```

## 适配其他学校

默认值是江苏大学（UJS）的：

- mycos 入口：`https://jpv2-2.mycospxk.com/wx/ver3.41.0/index.html`
- SSO IPv4 强制：`MAP *.ujs.edu.cn 218.3.94.83`（绕过 Happy Eyeballs 在无 IPv6
  路由网络下的 ERR_TIMED_OUT）

其他学校：

```bash
# 1. 找到自己学校的 mycos 入口（一般也是 jpvN-M.mycospxk.com 系列）
.venv/bin/python eval.py run --url "https://jpv1-3.mycospxk.com/wx/ver3.41.0/index.html"

# 2. 如果 SSO 不需 IPv4 强制，关掉 resolver 规则
.venv/bin/python eval.py --resolver "" run --text "..."

# 3. 如果你学校 SSO 也有 IPv6 卡顿问题，换成自己学校的规则
.venv/bin/python eval.py \
    --resolver "MAP *.your-school.edu.cn x.x.x.x" \
    run --text "..."
```

`--resolver` 是放在子命令前的全局参数。

## 已知 mycos 反作弊行为

| 行为 | 触发条件 | skill 应对 |
|---|---|---|
| `不允许提交满分评价` | 所有单选都选最高档 | 强制随机 3 道选次高档 |
| `您对该教师的评价较高，请填写原因` | 整体评分偏高 | 检测 modal 自动填理由再点确定 |
| 首页不隐藏已完成卡片 | 完成后再回首页 | run-local blacklist 跳过 |

## 项目结构

```
mycos-eval-skill/
├── SKILL.md               # Claude Code skill 元信息和指令
├── eval.py                # Playwright 自动化脚本
├── requirements.txt       # playwright
├── README.md              # 本文件
├── LICENSE                # MIT
└── .gitignore             # 忽略 .venv / .state / 等
```

`.state/` 目录由脚本运行时创建，存放：

- `storage_state.json`：登录后的浏览器会话（含 cookie，**敏感**，不要提交到
  git）
- `last_run.log`：最近一次跑的详细日志
- `screenshots/`：每个状态的截图，调试时翻这里

## 调试

- 跑出问题先看 `.state/screenshots/`，文件名带状态前缀（form-N、filled-N、
  success-N、unknown-N、submit-rejected-N 等），按时间倒序看就能定位
- 完整日志在 `.state/last_run.log`
- 加 `--headed` 直接看 Chrome 在干啥
- 学校的 mycos UI 改版导致选择器失效是最常见的事故源；改 `eval.py` 顶部那几个
  `*_JS` 字符串里的 selector

## License

MIT — see [LICENSE](LICENSE).
