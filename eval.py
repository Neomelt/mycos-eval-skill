#!/usr/bin/env python3
"""
mycospxk.com 教学评价自动化。

用法：
  eval.py login              首次：打开浏览器让用户登录，保存会话
  eval.py check              检查会话是否有效（headless）
  eval.py run [选项]         主流程：扫描所有未完成评教并自动填写提交
    --comments PATH          评语 JSON 文件（数组），不传则用内置评语
    --dry-run                填好但不提交
    --headed                 显示浏览器窗口（调试用）
    --moderate               随机混入第二档评分（不全是非常满意）
    --max N                  最多处理 N 条
    --url URL                覆盖默认入口 URL

会话与日志存放在脚本同目录的 .state/ 下。
"""

import argparse
import asyncio
import json
import random
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

# ---------------- 路径与常量 ----------------
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPT_DIR / ".state"
STATE_DIR.mkdir(exist_ok=True)
STORAGE_FILE = STATE_DIR / "storage_state.json"
LOG_FILE = STATE_DIR / "last_run.log"
SHOT_DIR = STATE_DIR / "screenshots"
SHOT_DIR.mkdir(exist_ok=True)

DEFAULT_URL = "https://jpv2-2.mycospxk.com/wx/ver3.41.0/index.html"

# 默认 Chrome host-resolver-rules：把江苏大学 SSO 域名强制走 IPv4，
# 绕过 Happy Eyeballs 在无 IPv6 路由网络下的 ERR_TIMED_OUT。
# 其他学校的 mycos 不一定需要；可在命令行用 --resolver "" 关闭，
# 或换成自己学校 SSO 的对应规则。
DEFAULT_RESOLVER_RULES = "MAP *.ujs.edu.cn 218.3.94.83"

# 全局，由 main() 读 --resolver 设置。new_context 会读这个加到 chrome_args。
RESOLVER_RULES: str | None = None

WX_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.32(0x18002028) NetType/WIFI Language/zh_CN"
)

# 注入到 page 的 init 脚本：伪装微信环境，部分 wx 版页面会做嗅探
WX_INIT_JS = r"""
window.__wxjs_environment = 'miniprogram';
if (!window.WeixinJSBridge) {
  window.WeixinJSBridge = {
    invoke: function(){}, on: function(){}, call: function(){},
  };
}
"""

FALLBACK_COMMENTS = [
    "课程整体节奏合理，知识点之间衔接自然，跟下来感觉很顺。",
    "老师讲课很有条理，复杂概念讲得清楚，课堂氛围也比较轻松。",
    "教学方式比较灵活，案例和知识结合得不错，学到了一些新思路。",
    "答疑很耐心，提的问题都能得到具体回应，这点挺加分。",
    "一学期下来对这门学科的理解更清晰了，方法论上有提升。",
    "板书结构层次分明，重点和难点划分得很清楚，复习起来方便。",
    "课堂节奏不疾不徐，给学生留了消化时间，不会一味赶进度。",
    "课外资料推荐得很合时宜，对感兴趣的话题可以接着深挖。",
    "希望以后能多一些课堂讨论环节，互动起来收获会更大。",
    "老师对学生的提问态度很开放，不太懂的地方愿意反复解释。",
    "考核方式设计得比较平衡，过程和结果都有体现。",
    "能感觉到老师对这门课很有热情，讲到关键处会停下来强调。",
    "课程结构和大纲基本一致，没有跑题或者临时大幅调整。",
    "老师的讲解贴合实际，举的例子能让人产生联想。",
    "个人觉得这门课对未来相关方向的学习有铺垫作用。",
    "课件设计简洁，没有信息堆砌，看着不累。",
    "对一些容易混淆的概念，老师特意花时间做了对比说明。",
    "如果能再多一点小练习就更好，方便学生即时检验掌握程度。",
    "整体氛围比较开放，提问不会有压力。",
    "老师的表达节奏稳，听课不容易走神。",
]

LOG_LINES: list[str] = []


def log(msg: str, level: str = "info") -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}"
    LOG_LINES.append(line)
    print(line, file=sys.stderr, flush=True)


def flush_log() -> None:
    LOG_FILE.write_text("\n".join(LOG_LINES), encoding="utf-8")


# ---------------- 浏览器上下文构造 ----------------

async def new_context(p, headless: bool, with_storage: bool, mode: str = "mobile-wx") -> tuple:
    """
    mode:
      'desktop'    桌面 Chrome 默认 UA + 1280x900 视口；用于登录时与 SSO 兼容
      'mobile-wx'  iPhone+WeChat 伪装 + 390x844 视口；用于跑 wx 版评教页面

    全局 RESOLVER_RULES（由 main 根据 --resolver 设置）若非空，
    会作为 `--host-resolver-rules=<...>` 传给 Chrome。典型用途：学校 SSO
    （如 *.ujs.edu.cn）DNS 同时返回 IPv4 和 IPv6，但你网络无 IPv6 出口，
    Chrome 走 Happy Eyeballs 卡住 ERR_TIMED_OUT —— 用这个强制走 IPv4。
    """
    chrome_args = ["--disable-blink-features=AutomationControlled"]
    if RESOLVER_RULES:
        chrome_args.append(f"--host-resolver-rules={RESOLVER_RULES}")
    browser = await p.chromium.launch(
        channel="chrome",
        headless=headless,
        args=chrome_args,
    )
    storage = str(STORAGE_FILE) if with_storage and STORAGE_FILE.exists() else None
    if mode == "desktop":
        context = await browser.new_context(
            storage_state=storage,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
    else:
        context = await browser.new_context(
            storage_state=storage,
            user_agent=WX_UA,
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(WX_INIT_JS)
    return browser, context


async def shoot(page: Page, name: str) -> Path:
    path = SHOT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception as e:
        log(f"截图失败 {name}: {e}", "warn")
    return path


# ---------------- 子命令: login ----------------

async def cmd_login(url: str) -> int:
    """打开有头浏览器让用户登录。检测到登录完成或哨兵文件出现后保存会话。"""
    proceed_file = STATE_DIR / "proceed"
    if proceed_file.exists():
        proceed_file.unlink()

    async with async_playwright() as p:
        # 登录用桌面模式：避免学校 SSO 因为微信环境伪装而白屏
        browser, context = await new_context(p, headless=False, with_storage=False, mode="desktop")
        page = await context.new_page()
        log(f"打开 {url}（桌面模式）")
        await page.goto(url, wait_until="domcontentloaded")
        initial_url = page.url

        print(
            f"\n>>> 浏览器已弹出。完成登录后会自动检测保存会话。\n"
            f"    若 URL 自动检测失败，可让 Claude 创建哨兵文件：touch {proceed_file}\n",
            file=sys.stderr, flush=True,
        )

        timeout_s = 600  # 10 分钟
        elapsed = 0
        interval = 2
        detected_reason = None
        while elapsed < timeout_s:
            # 1) 哨兵文件
            if proceed_file.exists():
                detected_reason = "sentinel file"
                break
            # 2) URL 自动检测：hash 变成 app 路由（不含 login/auth）
            try:
                cur = page.url
                cur_lower = cur.lower()
                hash_part = cur.split("#", 1)[1] if "#" in cur else ""
                if hash_part.startswith("/") and not any(
                    k in cur_lower for k in ("login", "auth", "signin", "wxlogin", "callback")
                ):
                    # 仅当 hash 路径与初始访问的 hash 不同时认定为登录后的导航
                    initial_hash = initial_url.split("#", 1)[1] if "#" in initial_url else ""
                    if hash_part != initial_hash and hash_part not in ("/", ""):
                        # 等 2 秒确认 URL 稳定
                        await asyncio.sleep(2)
                        if page.url == cur:
                            detected_reason = f"url hash {hash_part}"
                            break
            except Exception:
                pass
            # 3) 浏览器被关掉？
            try:
                if page.is_closed():
                    log("浏览器被关闭，放弃登录", "err")
                    await browser.close()
                    return 1
            except Exception:
                break
            await asyncio.sleep(interval)
            elapsed += interval

        if elapsed >= timeout_s:
            log("登录等待超时（10分钟）", "err")
            await browser.close()
            return 1

        log(f"检测到已登录（{detected_reason}），保存会话", "ok")
        await context.storage_state(path=str(STORAGE_FILE))
        if proceed_file.exists():
            proceed_file.unlink()
        log(f"会话已保存到 {STORAGE_FILE}", "ok")
        await browser.close()
    return 0


# ---------------- 子命令: check ----------------

async def cmd_check(url: str) -> int:
    if not STORAGE_FILE.exists():
        print("none")
        return 2
    async with async_playwright() as p:
        # 使用与 login 一致的 desktop 模式：cookie 域名级生效，但页面 wx 版会嗅探微信环境
        browser, context = await new_context(p, headless=True, with_storage=True, mode="desktop")
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2500)
            html = (await page.content()).lower()
            page_url = page.url.lower()
            # 启发式：登录页通常包含 "login" 或 "授权" 字样
            if any(k in page_url for k in ("login", "auth", "wxlogin")):
                print("expired")
                rc = 1
            elif "登录" in html and "退出" not in html:
                print("expired")
                rc = 1
            else:
                print("valid")
                rc = 0
        except Exception as e:
            log(f"check 异常: {e}", "err")
            print("error")
            rc = 3
        finally:
            await browser.close()
    return rc


# ---------------- 表单识别与填写 ----------------

# 判断当前页处于哪一阶段。优先级：弹窗 > 表单 > 列表 > 首页
# mycos 同时混用 ant-modal（PC）和 am-modal（移动）两套弹窗
PAGE_STATE_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  const text = el => (el.textContent || '').trim();

  const modals = [...document.querySelectorAll(
    '.ant-modal, .ant-modal-body, .ant-modal-confirm, .am-modal, .am-modal-content, .am-modal-body, [role="dialog"]'
  )].filter(visible);

  // 0a) 提交成功弹窗
  for (const m of modals) {
    const t = text(m);
    if (/提交成功|提交完成|评价完成|感谢您的参与/.test(t)) return 'success';
  }
  // 0b) 评价较高/填写原因弹窗
  for (const m of modals) {
    const t = text(m);
    if (/评价较高|填写原因|请说明|请填写说明/.test(t)) return 'reason-modal';
  }
  // 0c) 普通确认弹窗
  for (const m of modals) {
    const t = text(m);
    if (/^.{0,30}(确认|确定).{0,30}$/.test(t.replace(/\s/g, ''))) return 'plain-modal';
  }

  // 0d) 整份问卷完成总结页（"评价完成"+"查看详情"）
  // 在 modal 检测之后判断，所以这里到达时已经没有 modal
  // 不要 require !radio-wrapper：SPA 路由切换后旧页面 radio 残留是常见情况
  {
    const hasComplete = [...document.querySelectorAll('p, h1, h2, h3, h4, div, span')].some(e => {
      return /^评价完成$|^问卷已完成$|^您已完成本次评价$/.test(text(e)) && visible(e);
    });
    if (hasComplete) return 'survey-complete';
  }

  // 1) 表单页
  if (document.querySelector('.ant-radio-wrapper') &&
      [...document.querySelectorAll('button')].some(b => visible(b) && /^提\s*交$/.test(b.textContent.trim()))) {
    return 'form';
  }
  // 2) 课程列表页
  if ([...document.querySelectorAll('*')].some(e => {
    const t = text(e);
    return t.includes('授课教师') && t.length < 200 && t.length > 30 && visible(e);
  })) {
    return 'survey-list';
  }
  // 3) 首页
  if (document.querySelector('[class*="Card_root"]')) return 'home';

  return 'unknown';
}
"""

# "提交成功"对话框：点"下一门课程" span（只在 modal 内部找，不点页面 navbar 的"返回"）
HANDLE_SUCCESS_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  const text = el => (el.textContent || '').trim().replace(/\s+/g, '');
  const depthOf = el => { let d = 0; for (let p = el; p; p = p.parentElement) d++; return d; };

  // 只在 modal 内查找（不要点到页面外的"返回"导航）
  const modalSel = '.ant-modal, .ant-modal-body, .ant-modal-confirm, .am-modal, .am-modal-wrap, .am-modal-content, .am-modal-body, [role="dialog"]';
  const modals = [...document.querySelectorAll(modalSel)].filter(visible);
  if (modals.length === 0) return 'no-modal';

  // 取最大的 modal（最外层 wrap，包含所有内容）
  modals.sort((a, b) => {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    return (rb.width * rb.height) - (ra.width * ra.height);
  });

  for (const m of modals) {
    const cands = [...m.querySelectorAll('span, div, button, a, [role="button"]')].filter(visible);
    if (cands.length === 0) continue;

    // 1) "下一门课程"
    const next = cands.filter(e => /^下一门?课程?$/.test(text(e)));
    if (next.length > 0) {
      next.sort((a, b) => depthOf(b) - depthOf(a));
      next[0].click();
      return 'next-course';
    }

    // 2) 在 modal 内找完成/关闭/我知道了（去掉"返回"！避免误点 navbar）
    const close = cands.filter(e => /^(关闭|完成|我知道了|确定$)/.test(text(e)));
    if (close.length > 0) {
      close.sort((a, b) => depthOf(b) - depthOf(a));
      close[0].click();
      return 'closed';
    }
  }

  // 3) am/ant modal 自带 X 关闭按钮
  const x = document.querySelector('.ant-modal-close, .ant-modal-close-x, .am-modal-close, .am-modal-close-x');
  if (x) { x.click(); return 'x'; }
  return 'none';
}
"""

# "评价较高"对话框：填理由 + 点确定
HANDLE_REASON_JS = r"""
({reason}) => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  const setNativeValue = (el, v) => {
    const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
    if (desc && desc.set) desc.set.call(el, v); else el.value = v;
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const modals = [...document.querySelectorAll('.ant-modal, [role="dialog"]')].filter(visible);
  for (const m of modals) {
    if (!/评价较高|填写原因|请说明/.test(m.textContent || '')) continue;
    const ta = m.querySelector('textarea');
    if (ta) setNativeValue(ta, reason);
    const ok = [...m.querySelectorAll('button')].find(
      b => visible(b) && /^(确\s*定|确\s*认|提\s*交)$/.test((b.textContent||'').trim())
    );
    if (ok) { ok.click(); return 'filled-confirmed'; }
    return 'filled-no-confirm-btn';
  }
  return 'no-reason-modal';
}
"""

# Toast 错误检测
ERROR_TOAST_JS = r"""
() => {
  const errs = [...document.querySelectorAll('.ant-message-error, .ant-message-warning')]
    .filter(e => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  if (errs.length === 0) return null;
  return errs.map(e => (e.textContent || '').trim()).join(' | ');
}
"""

# 在首页：点击下一个未提交问卷卡片，blacklist 排除本次已点过且无可评内容的卡片
CLICK_HOME_NEXT_JS = r"""
({blacklist}) => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  const cards = [...document.querySelectorAll('[class*="Card_root"]')].filter(visible);
  if (cards.length === 0) return {ok: false, reason: 'no card'};
  const target = cards.find(c => {
    const t = (c.textContent || '');
    if (!/问卷|评价|评教/.test(t)) return false;
    if (/已完成|已提交|已结束|已截止/.test(t)) return false;
    // 本次运行已尝试过且无可评内容
    const fp = t.trim().slice(0, 80);
    if (blacklist.some(b => fp.includes(b) || b.includes(fp))) return false;
    return true;
  });
  if (!target) return {ok: false, reason: 'no pending survey on home (after blacklist)'};
  const txt = target.textContent.trim().slice(0, 80);
  target.scrollIntoView({block: 'center'});
  target.click();
  return {ok: true, text: txt};
}
"""

# 在课程列表页：点击下一个待评课程卡片（必须含"进行中"，正向过滤）
CLICK_SURVEY_NEXT_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  const text = el => (el.textContent || '').trim().replace(/\s+/g, ' ');

  // 正向过滤：必须同时含"授课教师"和"进行中"
  const candidates = [...document.querySelectorAll('*')].filter(e => {
    if (!visible(e)) return false;
    const t = text(e);
    if (!t.includes('授课教师')) return false;
    if (!t.includes('进行中')) return false;
    if (t.length > 250) return false;
    return true;
  });
  if (candidates.length === 0) return {ok: false, reason: 'no in-progress course'};
  candidates.sort((a, b) => text(a).length - text(b).length);
  let target = candidates[0];
  let p = target;
  while (p && p !== document.body) {
    const h = p.getBoundingClientRect().height;
    if (h >= 60 && h <= 250) { target = p; break; }
    p = p.parentElement;
  }
  const txt = text(target).slice(0, 80);
  target.scrollIntoView({block: 'center'});
  target.click();
  return {ok: true, text: txt};
}
"""

# 兼容旧名字（其他地方用到）
IS_FORM_PAGE_JS = r"""
() => !!document.querySelector('.ant-radio-wrapper') &&
       [...document.querySelectorAll('button')].some(b => /提\s*交/.test(b.textContent))
"""

# 填写当前表单页（Ant Design）：单选选最高档 + 评语填到 textarea
# 选项顺序是 非常差/差/一般/好/非常好，所以默认取每组最后一个（pickIdx = -1）
FILL_FORM_JS = r"""
({mainText, suggestionText, pickIdx, moderate}) => {
  const rng = Math.random;

  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };

  const setNativeValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  };

  const result = { radios: 0, textareas: 0 };

  // === Ant Design 单选 ===
  let groups = [...document.querySelectorAll('.ant-radio-group')].filter(visible);
  if (groups.length === 0) {
    const allWrappers = [...document.querySelectorAll('.ant-radio-wrapper')].filter(visible);
    const byParent = new Map();
    for (const w of allWrappers) {
      const p = w.parentElement;
      if (!byParent.has(p)) byParent.set(p, []);
      byParent.get(p).push(w);
    }
    groups = [...byParent.entries()].filter(([_, arr]) => arr.length >= 2).map(([p]) => p);
  }

  // 反"满分作答"：随机选 3 个组改成次高档（idx-1）。
  // moderate 时改成 40%（更分散）。
  const totalGroups = groups.length;
  const nonMaxCount = moderate
    ? Math.max(3, Math.round(totalGroups * 0.4))
    : Math.min(3, totalGroups);
  const nonMaxSet = new Set();
  while (nonMaxSet.size < nonMaxCount && nonMaxSet.size < totalGroups) {
    nonMaxSet.add(Math.floor(rng() * totalGroups));
  }

  groups.forEach((g, gi) => {
    const wrappers = [...g.querySelectorAll('.ant-radio-wrapper')].filter(visible);
    if (wrappers.length === 0) return;
    let idx;
    if (pickIdx === -1 || pickIdx === undefined) {
      // 默认：最后一项（最高档），但 nonMaxSet 里的组改成次高档
      idx = nonMaxSet.has(gi) ? Math.max(0, wrappers.length - 2) : wrappers.length - 1;
    } else {
      // 显式 pickIdx
      idx = pickIdx < 0 ? wrappers.length + pickIdx : pickIdx;
    }
    idx = Math.max(0, Math.min(wrappers.length - 1, idx));
    const target = wrappers[idx];
    const inp = target.querySelector('input[type="radio"]');
    if (inp) inp.click(); else target.click();
    result.radios++;
  });

  // === 文本框（Ant Input textarea）===
  // 对每个 textarea 找最近的题干文字。若题干含"建议/意见/想法/看法/想说/希望"等，
  // 一律填"无"（用户偏好：凑数问卷的建议题统一填无）。
  const findQuestionText = (ta) => {
    let p = ta.parentElement;
    for (let depth = 0; depth < 6 && p && p !== document.body; depth++) {
      const candidates = [...p.children].filter(c => {
        if (c === ta || c.contains(ta)) return false;
        const t = (c.textContent || '').replace(/\s+/g, ' ').trim();
        return t.length > 3 && t.length < 200 && /[一-龥]/.test(t);
      });
      if (candidates.length > 0) {
        return candidates.map(c => (c.textContent||'').replace(/\s+/g,' ').trim()).join(' ').slice(0, 200);
      }
      p = p.parentElement;
    }
    return '';
  };

  const SUGGESTION_RE = /建议|意见|想法|看法|有何|想说|希望|您还|您有|意 见|您觉得|您认为(.{0,20}还|.{0,20}需要)/;

  const tas = [...document.querySelectorAll('textarea')].filter(visible);
  for (const ta of tas) {
    const q = findQuestionText(ta);
    const value = (q && SUGGESTION_RE.test(q)) ? suggestionText : mainText;
    setNativeValue(ta, value);
    result.textareas++;
  }

  return result;
}
"""

CLICK_SUBMIT_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  // Ant Design：button.ant-btn-primary 含 "提 交"（中间可能空格）
  const btns = [...document.querySelectorAll('button')]
    .filter(visible)
    .filter(b => /^(提\s*交|确认提交|确认并提交|提交评价|提交问卷)$/.test((b.textContent || '').trim()));
  if (btns.length === 0) return false;
  btns[0].click();
  return true;
}
"""

CLICK_CONFIRM_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
  };
  // Ant Modal/Popconfirm 确认按钮
  const btns = [...document.querySelectorAll('.ant-modal button, .ant-popover button, .ant-popconfirm button, button')]
    .filter(visible)
    .filter(b => /^(确\s*定|确\s*认|是|继\s*续|好\s*的|OK)$/i.test((b.textContent || '').trim()));
  if (btns.length === 0) return false;
  btns[0].click();
  return true;
}
"""


# ---------------- 子命令: run ----------------

async def cmd_run(args: argparse.Namespace) -> int:
    # 决定主文本（非"建议"类文本框统一填的内容）
    main_text = args.text
    if not main_text and args.comments:
        try:
            data = json.loads(Path(args.comments).read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                main_text = random.choice([x for x in data if isinstance(x, str)])
                log(f"从 --comments 文件随机选了一条作为主文本: {main_text[:30]}")
        except Exception as e:
            log(f"读 comments 文件失败：{e}", "warn")
    if not main_text:
        main_text = "无意见"
        log("未指定 --text，使用默认 '无意见'", "warn")
    suggestion_text = args.suggestion_text or "无"
    log(f"主文本='{main_text[:40]}' 建议题='{suggestion_text}'")

    if not STORAGE_FILE.exists():
        log("未找到登录会话，先运行 `eval.py login`", "err")
        return 2

    async with async_playwright() as p:
        browser, context = await new_context(
            p, headless=not args.headed, with_storage=True, mode="desktop"
        )
        page = await context.new_page()

        log(f"打开 {args.url}（desktop 模式）")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
        except PWTimeout:
            log("入口页加载超时", "err")
            await shoot(page, "goto-timeout")
            await browser.close()
            return 3

        await page.wait_for_timeout(2500)

        # 登录态校验
        cur_url = page.url.lower()
        if any(k in cur_url for k in ("login", "auth", "wxlogin")):
            log("会话已过期，请重新运行 login", "err")
            await shoot(page, "expired")
            await browser.close()
            return 4

        await shoot(page, "home-initial")

        done = 0
        skipped = 0
        max_n = args.max
        idle_iters = 0
        form_attempts = 0  # 同一表单累计提交尝试次数
        prev_state = None
        success_dismiss_attempts = 0  # 连续 success 状态下尝试关闭对话框的次数
        home_blacklist: list[str] = []  # 已点过且无可评内容的问卷卡片指纹
        last_home_pick: str = ""  # 上次从首页点了哪个卡片
        for it in range(200):
            if done + skipped >= max_n:
                log(f"达到 max ({max_n})，停止", "ok")
                break
            state = await page.evaluate(PAGE_STATE_JS)
            log(f"[iter {it}] state={state} (done={done} skipped={skipped} prev={prev_state})")
            # 抓取后立刻更新 prev_state，dispatch 用局部 previous 比较
            previous = prev_state
            prev_state = state

            if state == "success":
                # 仅在首次进入 success 时计 done++（防止关不掉对话框时重复计数）
                if previous != "success":
                    done += 1
                    form_attempts = 0
                    success_dismiss_attempts = 0
                    log(f"✅ 已提交第 {done} 条", "ok")
                    await shoot(page, f"success-{done}")
                success_dismiss_attempts += 1
                action = await page.evaluate(HANDLE_SUCCESS_JS)
                log(f"成功弹窗操作: {action} (attempt {success_dismiss_attempts})")
                if action == "none" and success_dismiss_attempts >= 2:
                    # 兜底：按 Escape，再不行就 history.back
                    log("尝试 ESC 关闭对话框", "warn")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(1000)
                    if success_dismiss_attempts >= 4:
                        log("无法关闭成功对话框，go_back 强制退出", "err")
                        await go_back(page)
                        await page.wait_for_timeout(1500)
                await page.wait_for_timeout(2000)
                idle_iters = 0

            elif state == "reason-modal":
                # "评价较高，请填写原因" → 用主文本作为理由
                action = await page.evaluate(HANDLE_REASON_JS, {"reason": main_text})
                log(f"高评价原因弹窗: {action} (理由='{main_text[:20]}...')")
                await page.wait_for_timeout(1500)
                idle_iters = 0

            elif state == "plain-modal":
                clicked = await page.evaluate(CLICK_CONFIRM_JS)
                log(f"普通确认弹窗 clicked={clicked}")
                await page.wait_for_timeout(1500)
                idle_iters = 0

            elif state == "form":
                idle_iters = 0
                if form_attempts >= 3:
                    log("同一表单尝试超过 3 次，跳过", "err")
                    skipped += 1
                    form_attempts = 0
                    await go_back(page)
                    await page.wait_for_timeout(1500)
                    continue

                if form_attempts == 0:
                    await shoot(page, f"form-{done+skipped}")
                form_attempts += 1

                try:
                    fill_result = await page.evaluate(
                        FILL_FORM_JS,
                        {
                            "mainText": main_text,
                            "suggestionText": suggestion_text,
                            "pickIdx": -1,
                            "moderate": bool(args.moderate),
                        },
                    )
                    log(
                        f"填写：单选 {fill_result['radios']} 组，文本 {fill_result['textareas']} 个"
                    )
                    if fill_result["radios"] == 0 and fill_result["textareas"] == 0:
                        log("表单未识别出可填项", "err")
                        await shoot(page, f"empty-form-{done+skipped}")
                        skipped += 1
                        form_attempts = 0
                        await go_back(page)
                        await page.wait_for_timeout(1500)
                        continue
                except Exception as e:
                    log(f"填写异常: {e}", "err")
                    await shoot(page, f"fill-error-{done+skipped}")
                    skipped += 1
                    form_attempts = 0
                    await go_back(page)
                    continue

                await page.wait_for_timeout(400)
                if form_attempts == 1:
                    await shoot(page, f"filled-{done+skipped}")

                if args.dry_run:
                    log("dry-run：不提交", "ok")
                    done += 1
                    form_attempts = 0
                    await go_back(page)
                    await page.wait_for_timeout(1500)
                    continue

                submitted = await page.evaluate(CLICK_SUBMIT_JS)
                if not submitted:
                    log("找不到提交按钮", "err")
                    await shoot(page, f"no-submit-{done+skipped}")
                    skipped += 1
                    form_attempts = 0
                    await go_back(page)
                    continue
                await page.wait_for_timeout(1500)

                # 检测错误 toast（不允许全满分等）
                err_msg = await page.evaluate(ERROR_TOAST_JS)
                if err_msg:
                    log(f"提交被系统拒绝: {err_msg}", "err")
                    await shoot(page, f"submit-rejected-{done+skipped}")
                    # 留在表单页，下一 iter 还是 form 状态，会重试（至多 3 次）

            elif state == "survey-complete":
                idle_iters = 0
                form_attempts = 0
                # 如果直接从 form 跳到 survey-complete（最后一门课提交无中间 modal），
                # 这门课的 done 漏算了，补上
                if previous == "form":
                    done += 1
                    log(f"✅ 已提交第 {done} 条（合并入问卷完成页）", "ok")
                log("当前问卷全部完成，返回首页", "ok")
                await shoot(page, f"survey-complete-{done}")
                await go_back(page)
                await page.wait_for_timeout(2500)

            elif state == "survey-list":
                idle_iters = 0
                form_attempts = 0
                clicked = await page.evaluate(CLICK_SURVEY_NEXT_JS)
                if clicked.get("ok"):
                    log(f"进入课程: {clicked.get('text', '')[:60]}")
                    await page.wait_for_timeout(2500)
                else:
                    # 这个 survey 已经全部完成（找不到"进行中"课程）
                    if last_home_pick and last_home_pick not in home_blacklist:
                        home_blacklist.append(last_home_pick)
                        log(f"加入 blacklist: {last_home_pick[:50]}", "warn")
                    log(f"课程列表无更多待评 ({clicked.get('reason', '')})，返回首页", "ok")
                    await go_back(page)
                    await page.wait_for_timeout(1500)

            elif state == "home":
                idle_iters = 0
                form_attempts = 0
                clicked = await page.evaluate(CLICK_HOME_NEXT_JS, {"blacklist": home_blacklist})
                if clicked.get("ok"):
                    last_home_pick = clicked.get("text", "")
                    log(f"进入问卷: {last_home_pick[:60]}")
                    await page.wait_for_timeout(2500)
                else:
                    log(f"首页无更多待评问卷 ({clicked.get('reason', '')})", "ok")
                    break

            else:
                idle_iters += 1
                log(f"未知页面状态 (idle {idle_iters})", "warn")
                await shoot(page, f"unknown-{it}")
                if idle_iters >= 3:
                    log("连续多次未知状态，停止", "err")
                    break
                await page.wait_for_timeout(1500)

        await shoot(page, "final")
        log(f"完成：成功 {done}，跳过 {skipped}", "ok")
        await browser.close()
        flush_log()
        # 给上层一个机器可读的结果
        print(json.dumps({"done": done, "skipped": skipped}, ensure_ascii=False))
        return 0


async def wait_for_js(page: Page, expr: str, timeout_ms: int = 8000) -> bool:
    """轮询执行一段返回 bool 的 JS，直到为 true 或超时。"""
    elapsed = 0
    interval = 250
    while elapsed < timeout_ms:
        try:
            v = await page.evaluate(expr)
            if v:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(interval)
        elapsed += interval
    return False


async def go_back(page: Page) -> None:
    """尝试返回到列表页：先点页面里的返回箭头，否则用 history.back()。"""
    try:
        clicked = await page.evaluate(
            r"""
            () => {
              const cands = [...document.querySelectorAll(
                '.van-nav-bar__left, [class*="back"], [class*="Back"], [class*="arrow-left"]'
              )].filter(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              });
              if (cands.length) { cands[0].click(); return true; }
              return false;
            }
            """
        )
        if not clicked:
            await page.go_back()
    except Exception:
        try:
            await page.go_back()
        except Exception:
            pass


# ---------------- main ----------------

def main() -> int:
    parser = argparse.ArgumentParser(description="麦可思教学评价自动化")
    parser.add_argument(
        "--resolver",
        default=DEFAULT_RESOLVER_RULES,
        help=(
            f"传给 Chrome 的 --host-resolver-rules 内容，强制特定域名走指定 IP。"
            f"默认 {DEFAULT_RESOLVER_RULES!r}（江苏大学 SSO 走 IPv4 的兜底）。"
            "其他学校如 SSO 不需此处理可传空字符串：--resolver ''"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="打开浏览器手动登录并保存会话")
    p_login.add_argument("--url", default=DEFAULT_URL)

    p_check = sub.add_parser("check", help="检查会话有效性")
    p_check.add_argument("--url", default=DEFAULT_URL)

    p_run = sub.add_parser("run", help="自动跑完所有未完成评教")
    p_run.add_argument("--url", default=DEFAULT_URL)
    p_run.add_argument(
        "--text",
        help="非'建议/意见'类文本框统一填这一句（推荐 8-15 字短句）",
    )
    p_run.add_argument(
        "--suggestion-text",
        default="无",
        help="'建议/意见/希望'类问题填什么（默认'无'）",
    )
    p_run.add_argument("--comments", help="[兼容旧版] 评语 JSON 数组文件，会随机选一条作为 --text")
    p_run.add_argument("--dry-run", action="store_true", help="填好但不提交")
    p_run.add_argument("--headed", action="store_true", help="显示浏览器（调试）")
    p_run.add_argument("--moderate", action="store_true", help="混入第二档评分")
    p_run.add_argument("--max", type=int, default=99, help="最多处理多少条")

    args = parser.parse_args()

    # 设置全局 resolver rules，让 new_context 读
    global RESOLVER_RULES
    RESOLVER_RULES = args.resolver or None
    if RESOLVER_RULES:
        log(f"Chrome host-resolver-rules: {RESOLVER_RULES}")

    try:
        if args.cmd == "login":
            return asyncio.run(cmd_login(args.url))
        if args.cmd == "check":
            return asyncio.run(cmd_check(args.url))
        if args.cmd == "run":
            return asyncio.run(cmd_run(args))
    except KeyboardInterrupt:
        log("用户中断", "err")
        flush_log()
        return 130
    except Exception as e:
        log(f"未捕获异常：{e}", "err")
        log(traceback.format_exc(), "err")
        flush_log()
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
