"""AgentGuard - deterministic safety layer for agent-facing HTTP endpoints.

Built for the 7th China AI Competition "Agent Safety Protection Challenge"
(智能体安全防护挑战赛, https://ai.xm.gov.cn/), whose official rubric scores
four dimensions:

  1. 权限访问控制   privilege / access control
  2. 提示词注入防护 prompt-injection defense
  3. 隐私信息保护   privacy / PII protection
  4. 误报率         false-positive rate on benign traffic

Design rules (inherited from AuditPilot):
- Zero dependencies (Python standard library only).
- Deterministic: same input -> same verdict. No model calls, no network.
- Never fabricate: every finding carries the rule id and the matched span.
- Benign traffic must pass. The false-positive calibration below is derived
  from five real misjudgement classes observed on the August adversarial
  message set (see orchestrate-kit/GOLDEN_PLAYBOOK.md):
    msg_107 prompt injection, msg_048 QR-phishing overreach, msg_099
    negation-context false hit, msg_008 marketing miss, msg_070
    non-Latin script accidental match.
  Those five traps are encoded as tests in test_agentguard.py.

What counts as the access-control dimension at this layer:
  - Bearer-token gating lives in china_ai_service.py (transport level);
  - crossing a privilege boundary (tool coercion, credential access,
    exfiltration) is detected here and refused;
  - every decision is emitted as one structured log line (disposal/处置).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# data structures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One rule hit. `span` is (start, end) into the original text."""

    category: str          # injection|jailbreak|smuggling|tool_coercion|exfiltration|prompt_echo|privilege|pii
    severity: str          # high|medium|low
    rule: str              # stable rule id, e.g. "inj.override.en"
    evidence: str          # matched snippet (trimmed)
    span: Tuple[int, int]
    context: str = "direct"  # direct|educational (educational = quoted for teaching)


@dataclass
class Verdict:
    decision: str          # allow|refuse|sanitize
    risk: int              # 0..100
    findings: List[Finding] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    pii: List[dict] = field(default_factory=list)
    redacted: Optional[str] = None
    latency_ms: float = 0.0
    text_len: int = 0

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "risk": self.risk,
            "categories": sorted({f.category for f in self.findings if f.context == "direct"}),
            "findings": [
                {
                    "rule": f.rule,
                    "category": f.category,
                    "severity": f.severity,
                    "evidence": f.evidence,
                    "span": list(f.span),
                    "context": f.context,
                }
                for f in self.findings
            ],
            "reasons": self.reasons,
            "pii": self.pii,
            "redacted": self.redacted,
            "latency_ms": round(self.latency_ms, 3),
            "text_len": self.text_len,
        }


@dataclass(frozen=True)
class GuardPolicy:
    refuse_threshold: int = 55        # refuse at/above this accumulated risk
    echo_user_content: bool = False   # if True, responses echo (redacted) input
    redact_pii_in_logs: bool = True


DEFAULT_POLICY = GuardPolicy()

_SEV_WEIGHT = {"high": 45, "medium": 18, "low": 3}
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}

# --------------------------------------------------------------------------
# PII primitives (deterministic, checksum-verified)
# --------------------------------------------------------------------------

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _valid_cn_id(s: str) -> bool:
    if len(s) != 18:
        return False
    body, check = s[:17], s[17].upper()
    if not body.isdigit() or not check.isalnum():
        return False
    total = sum(int(b) * w for b, w in zip(body, _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == check


def _luhn_ok(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# A checksum-valid 18-digit id used by the offline test suite (fabricated
# body, valid check digit) - never a real person's number.
TEST_CN_ID = "11010519491231002X"

# --------------------------------------------------------------------------
# rule tables
# --------------------------------------------------------------------------
# (rule_id, category, severity, compiled_pattern)
# English rules are only applied to text that is actually Latin-script
# (see _script_gate) so Devanagari/Arabic lookalikes cannot false-hit
# (msg_070 lesson).

_EN_RULES: List[Tuple[str, str, str, "re.Pattern[str]"]] = [
    ("inj.override.en", "injection", "high",
     re.compile(r"ignore\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|earlier|preceding|initial)\s+(?:instructions?|prompts?|rules?|directions?|constraints?)", re.I)),
    ("inj.override.en2", "injection", "high",
     re.compile(r"disregard\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|your)\s+(?:instructions?|prompts?|rules?|guidelines?)", re.I)),
    ("inj.forget.en", "injection", "high",
     re.compile(r"forget\s+(?:everything|all)\s+(?:you|that)\s+(?:were\s+)?(?:told|instructed|learned)", re.I)),
    ("jail.dan.en", "jailbreak", "high",
     re.compile(r"\b(?:DAN|do\s+anything\s+now)\s+mode\b|\byou\s+are\s+(?:now\s+)?DAN\b", re.I)),
    ("jail.nodev.en", "jailbreak", "medium",
     re.compile(r"developer\s+mode", re.I)),
    ("jail.unrestricted.en", "jailbreak", "high",
     re.compile(r"(?:no|without)\s+(?:any\s+)?(?:longer\s+)?(?:restrictions?|filters?|limits?|constraints?|guardrails?)", re.I)),
    ("jail.bypass.en", "jailbreak", "high",
     re.compile(r"bypass\s+(?:your\s+)?(?:safety|security|content)\s+(?:filters?|checks?|rules?)", re.I)),
    ("smug.tag.en", "smuggling", "high",
     re.compile(r"<\s*/?\s*(?:system|instruction|assistant|human)\s*>|<\|\s*im_start\s*\|>|\[\s*/?\s*inst\s*\]", re.I)),
    ("smug.fence.en", "smuggling", "high",
     re.compile(r"```\s*system|###\s*(?:system|instruction|new\s+instructions?)\b|\bSYSTEM\s*:", re.I)),
    ("coerce.shell.en", "tool_coercion", "high",
     re.compile(r"\brm\s+-rf\b|\bcurl\b[^\n]{0,80}\|\s*(?:ba)?sh\b|\bos\.system\b|\bsubprocess\.|\beval\s*\(|\bexec\s*\(|:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.I)),
    ("coerce.exec.en", "tool_coercion", "medium",
     re.compile(r"(?:run|execute)\s+(?:this|the\s+following)\s+(?:shell|bash|python|sql)\s+(?:command|code|script|query)", re.I)),
    ("exfil.url.en", "exfiltration", "high",
     re.compile(r"(?:send|post|forward|upload|transmit)\s+(?:it|this|them|the\s+(?:data|conversation|history|file|result))?\s*(?:to|towards)\s+(?:https?://|www\.)|webhook\.site|requestbin|ngrok\.io", re.I)),
    ("echo.prompt.en", "prompt_echo", "medium",
     re.compile(r"(?:show|print|repeat|reveal|output|leak|tell\s+me)\s+(?:me\s+)?(?:your\s+|the\s+)*(?:system\s+)?(?:prompt|instructions?|configuration)", re.I)),
    ("priv.cred.en", "privilege", "high",
     re.compile(r"(?:api|secret|access|auth|private)\s*(?:_|-|\s)?key|id_rsa|\.ssh/|/etc/(?:passwd|shadow)", re.I)),
    ("priv.sudo.en", "privilege", "medium",
     re.compile(r"\bsudo\b|chmod\s+777|(?:grant|give)\s+(?:me\s+)?(?:admin|root|superuser)", re.I)),
]

_ZH_RULES: List[Tuple[str, str, str, "re.Pattern[str]"]] = [
    ("inj.override.zh", "injection", "high",
     re.compile(r"忽略.{0,8}(?:之前|以上|先前|上述|以前).{0,6}(?:指令|指示|设定|规则|要求|约束)")),
    ("inj.override.zh2", "injection", "high",
     re.compile(r"无视.{0,6}(?:系统|安全|上述).{0,4}(?:提示|设定|规则|限制)")),
    ("inj.forget.zh", "injection", "high",
     re.compile(r"忘记你的?(?:设定|指令|身份|规则)")),
    ("jail.dev.zh", "jailbreak", "high",
     re.compile(r"开发者模式|越狱模式|解除(?:所有|一切)?限制")),
    ("jail.role.zh", "jailbreak", "high",
     re.compile(r"你现在是(?:一个|一名)?.{0,14}(?:没有限制|不受约束|无限制)")),
    ("smug.tag.zh", "smuggling", "high",
     re.compile(r"<\s*/?\s*(?:系统|指令|助手)\s*>|###\s*(?:系统|新指令)|系统\s*[:：]\s*新(?:指令|设定)")),
    ("coerce.shell.zh", "tool_coercion", "high",
     re.compile(r"rm\s+-rf|格式化(?:磁盘|硬盘)|关机命令|执行(?:这段|以下)(?: shell|shell|命令|脚本|代码)")),
    ("exfil.zh", "exfiltration", "high",
     re.compile(r"(?:导出|整理|收集|汇总).{0,24}(?:发送|发到|发给|上传).{0,16}(?:邮箱|http|网址|链接|群|外部)")),
    ("exfil.zh2", "exfiltration", "high",
     re.compile(r"(?:把|将).{0,12}(?:信息|数据|记录|资料|内容|手机号|身份证|银行卡|账号|隐私|证件).{0,10}(?:发送|转发|上传).{0,16}(?:http|邮箱|网址|外部|群)")),
    ("echo.prompt.zh", "prompt_echo", "medium",
     re.compile(r"(?:输出|显示|打印|重复|泄露|告诉我).{0,10}(?:你的?|系统)?(?:系统)?(?:提示词|设定|指令|配置)")),
    ("priv.cred.zh", "privilege", "high",
     re.compile(r"(?:api|密钥|令牌|凭证|密码|账号密码)[key Key\s]*|读取\s*/etc|私钥|访问令牌")),
    ("priv.sudo.zh", "privilege", "medium",
     re.compile(r"提权|管理员权限|root\s*权限|最高权限")),
]

_PII_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("cn_mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("cn_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    # local part bounded at 200 chars: an unbounded `+` before `@` degrades
    # to O(n^2) backtracking on long non-email text (measured: 768ms/10KB).
    ("email", re.compile(r"[A-Za-z0-9._%+-]{1,200}@[A-Za-z0-9.-]{1,64}\.[A-Za-z]{2,24}")),
    ("cn_landline", re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")),
]
_DIGIT_PII = {"cn_mobile", "cn_id", "bank_card", "cn_landline"}

# Educational / defensive framing markers. A hit inside this window is
# recorded but downgraded - EXCEPT for the categories in _NO_DOWNGRADE:
# quoting an attack to teach about it is benign traffic, but an actual
# exfiltration / tool-coercion / credential-access instruction is harmful
# regardless of the framing wrapped around it (msg_107 lesson, inverted).
_EDU_MARKERS = re.compile(
    r"反诈|防骗|防诈|安全教育|安全宣传|科普|教学|举例|示例|比如|假设这是|模拟|演习|演练|"
    r"如何防范|怎么识别|识别(?:这|以下|这种)|讲解|测试用例|样本|"
    r"example|sample|explain|for .{0,24}(?:education|training|teaching|testing)|how to (?:spot|identify|detect|recognize)",
    re.I,
)

# These categories are never downgraded by educational framing.
_NO_DOWNGRADE = {"exfiltration", "tool_coercion", "privilege", "smuggling"}

_NEGATION_MARKERS = re.compile(r"不是|并非|没有|不属于|谈不上|非诈骗|正规|合法|官方")


def _script_gate(text: str) -> Dict[str, bool]:
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    non_latin = sum(1 for c in text if ord(c) > 0x0250)
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return {
        "en_ok": latin >= 6 or (non_latin + cjk > 0 and latin >= 6) or (non_latin == 0 and latin >= 6),
        "zh_ok": cjk >= 2,
    }


def _educational_context(text: str, span: Tuple[int, int], window: int = 40) -> bool:
    lo = max(0, span[0] - window)
    hi = min(len(text), span[1] + window)
    return bool(_EDU_MARKERS.search(text[lo:hi]))


def _snippet(text: str, span: Tuple[int, int], limit: int = 80) -> str:
    s = text[span[0]:span[1]].strip()
    return s[:limit] + ("..." if len(s) > limit else "")


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------


def _detect_rules(text: str) -> List[Finding]:
    gate = _script_gate(text)
    out: List[Finding] = []
    tables = []
    if gate["en_ok"]:
        tables.append(_EN_RULES)
    if gate["zh_ok"]:
        tables.append(_ZH_RULES)
    else:
        # Chinese rules are CJK-specific; still safe to run when no CJK.
        tables.append(_ZH_RULES)
    for table in tables:
        for rule_id, category, severity, pattern in table:
            for m in pattern.finditer(text):
                edu = _educational_context(text, m.span())
                sev = severity
                if edu and category not in _NO_DOWNGRADE:
                    # downgradable category quoted for teaching: keep the
                    # evidence, drop it out of the active risk pool.
                    sev = "low" if sev in ("high", "medium") else sev
                    ctx = "educational"
                else:
                    # no-downgrade category (exfiltration/tool_coercion/
                    # privilege/smuggling): an actual instruction stays
                    # active and high even inside educational framing.
                    ctx = "direct"
                out.append(
                    Finding(
                        category=category,
                        severity=sev,
                        rule=rule_id,
                        evidence=_snippet(text, m.span()),
                        span=m.span(),
                        context=ctx,
                    )
                )
    return out


def _detect_pii(text: str) -> List[Finding]:
    # Cheap content pre-filters keep long texts linear: digit patterns only
    # run when a digit exists, the email pattern only when "@" exists.
    has_digit = any(ch.isdigit() for ch in text)
    has_at = "@" in text
    active_patterns = [
        (ptype, pattern) for ptype, pattern in _PII_PATTERNS
        if (ptype != "email" and has_digit) or (ptype == "email" and has_at)
    ]
    out: List[Finding] = []
    id_spans: List[Tuple[int, int]] = []
    for ptype, pattern in active_patterns:
        for m in pattern.finditer(text):
            token = m.group(0)
            if ptype == "cn_id" and not _valid_cn_id(token):
                continue
            if ptype == "bank_card":
                if not _luhn_ok(token):
                    continue
                if any(a <= m.start() and m.end() <= b for a, b in id_spans):
                    continue  # already reported as an id card
            if ptype == "cn_id":
                id_spans.append(m.span())
            out.append(
                Finding(
                    category="pii",
                    severity="medium",
                    rule=f"pii.{ptype}",
                    evidence=_snippet(text, m.span()),
                    span=m.span(),
                )
            )
    return out


def _mask(token: str) -> str:
    if len(token) <= 4:
        return "****"
    return token[:3] + "****" + token[-4:]


def redact(text: str, findings: Optional[List[Finding]] = None) -> str:
    """Mask every PII span in `text`. Returns the redacted copy."""
    if findings is None:
        findings = _detect_pii(text)
    spans = sorted((f.span for f in findings if f.category == "pii"), reverse=True)
    out = text
    for start, end in spans:
        out = out[:start] + "[REDACTED]" + out[end:]
    return out


# --------------------------------------------------------------------------
# decision engine
# --------------------------------------------------------------------------


def guard_text(text: str, policy: Optional[GuardPolicy] = None) -> Verdict:
    policy = policy or DEFAULT_POLICY
    t0 = time.perf_counter()
    if not isinstance(text, str) or not text.strip():
        return Verdict(decision="allow", risk=0, text_len=0,
                       latency_ms=(time.perf_counter() - t0) * 1000)

    findings = _detect_rules(text) + _detect_pii(text)
    active = [f for f in findings if f.context == "direct"]
    pii = [f for f in active if f.category == "pii"]

    risk = sum(_SEV_WEIGHT[f.severity] for f in active)
    reasons: List[str] = []

    has_exfil = any(f.category == "exfiltration" for f in active)
    if has_exfil and pii:
        risk += 25
        reasons.append("exfiltration intent combined with PII in scope (+25)")
    medium_count = sum(1 for f in active if f.severity == "medium")
    if medium_count >= 3:
        risk += 10
        reasons.append(f"{medium_count} stacked medium-severity signals (+10)")
    risk = min(100, risk)

    high = [f for f in active if f.severity == "high"]
    if high:
        reasons.append(
            "high-severity signal: " + ", ".join(sorted({f.rule for f in high}))
        )
    if risk >= policy.refuse_threshold and not high:
        reasons.append(f"accumulated risk {risk} >= threshold {policy.refuse_threshold}")

    edu = [f for f in findings if f.context == "educational"]
    if edu:
        reasons.append(
            f"{len(edu)} attack pattern(s) quoted in educational/defensive context - downgraded"
        )

    if high or risk >= policy.refuse_threshold:
        decision = "refuse"
    elif policy.echo_user_content and pii:
        decision = "sanitize"
        reasons.append("PII masked before echoing user content")
    else:
        decision = "allow"
        if pii:
            reasons.append(
                f"{len(pii)} PII item(s) flagged and masked in logs (no refusal - input PII is not an attack)"
            )

    pii_report = [
        {"type": f.rule.split(".", 1)[1], "masked": _mask(f.evidence)} for f in pii
    ]
    redacted_text = redact(text, pii) if (pii and policy.redact_pii_in_logs) else None

    return Verdict(
        decision=decision,
        risk=risk,
        findings=findings,
        reasons=reasons,
        pii=pii_report,
        redacted=redacted_text,
        latency_ms=(time.perf_counter() - t0) * 1000,
        text_len=len(text),
    )


def guard_messages(messages: List[dict], policy: Optional[GuardPolicy] = None) -> Verdict:
    """Guard an OpenAI-style message array; scans every user message."""
    policy = policy or DEFAULT_POLICY
    if not isinstance(messages, list):
        return guard_text("", policy)
    texts: List[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        if role not in ("user", "system", "tool"):
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        if isinstance(content, str):
            texts.append(content)
    joined = "\n".join(texts)
    return guard_text(joined, policy)
