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

需要 Linux/macOS 桌面（首次登录要 GUI 弹 Chrome）、Python 3.10+、Google Chrome。

```bash
# 1. clone 到 Claude Code 的 skills 目录
git clone https://github.com/<your-github>/mycos-eval-skill.git \
    ~/.claude/skills/mycos-eval

# 2. 装 Playwright（不下载额外 chromium，复用系统 Chrome）
cd ~/.claude/skills/mycos-eval
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

注意 skill 目录名建议用 `mycos-eval`（与 SKILL.md 的 `name` 字段一致），不要带
`-skill` 后缀。

## 使用

在 Claude Code 里直接说一句中文就触发：

```
跑一下评教
```
```
帮我做教学评价
```
```
搞一下麦可思
```

第一次会让你登录学校 SSO 一次（Chrome 窗口会自动弹出），之后会话复用直到
cookie 过期。

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
