"""Standard-library Web Demo for the HOYA BIT market research agent."""

from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from .backtest import compare_variants
from .errors import AgentInputError
from .llm import configured_provider, llm_is_configured, llm_runtime_info
from .ohlcv import load_ohlcv
from .orchestrator import run, run_comparison


def load_dotenv(path: Path | None = None) -> list[str]:
    """Load `KEY=value` pairs from `.env` into os.environ without overriding existing
    shell variables. Returns the names that were applied. Missing file is not an error."""
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return []
    applied = []
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if not key or not value or key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


BASE_CSS = """
:root{--brand:#ff6b35;--brand-2:#ff9a63;--brand-ink:#e2571f;--brand-soft:#ffede3;--brand-line:#fbd9c4;
--ink:#16202b;--ink-2:#44515f;--muted:#8a97a6;--line:#f0e7e0;--line-2:#e6dad0;--surface:#fffaf6;--surface-2:#fff3ea;
--ok:#0e8a66;--ok-soft:#e9f8f2;--ok-line:#c9ecdf;--warn:#b4761a;--warn-soft:#fff4e2;--warn-line:#f7e2bf;
--bad:#c92a2f;--bad-soft:#feecec;--bad-line:#f7cfd0;--shadow:0 18px 44px rgba(22,32,43,.07);
font-family:Inter,"Noto Sans TC","Microsoft JhengHei",sans-serif;color:var(--ink);background:#fffaf6;color-scheme:light}
/* Viewport-anchored wash (background-attachment:fixed) so long report pages keep the peach
   gradient while scrolling instead of fading to flat white after the first screen. */
*{box-sizing:border-box}body{margin:0;min-height:100vh;-webkit-font-smoothing:antialiased;background-color:#fffaf6;
background-image:radial-gradient(1200px 780px at 92% -6%,#ffdfc9 0,rgba(255,240,230,.55) 34%,rgba(255,250,246,0) 68%),
radial-gradient(1000px 700px at -8% 108%,#ffe7d6 0,rgba(255,244,236,.45) 38%,rgba(255,250,246,0) 70%),
linear-gradient(180deg,#fff9f4 0,#fffaf6 100%);background-attachment:fixed;background-repeat:no-repeat}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
a{color:var(--brand-ink);text-decoration:none;transition:color .16s}a:hover{color:var(--brand)}
.wrap{width:min(1120px,calc(100% - 40px));margin:auto;padding:48px 0 72px}
.eyebrow{color:var(--brand);font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}
h1{font-size:clamp(34px,5vw,58px);line-height:1.08;margin:12px 0 16px;letter-spacing:-.035em}h2{margin:0 0 18px;font-size:21px}
.grad{background:linear-gradient(92deg,var(--brand),var(--brand-2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{max-width:740px;color:var(--ink-2);font-size:18px;line-height:1.75}.muted{color:var(--muted)}
.panel,.card{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}
.panel{padding:28px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}
.back{padding:11px 15px;border:1px solid var(--line-2);border-radius:11px;white-space:nowrap;color:var(--ink);background:#fff;font-weight:700;transition:.16s}
.back:hover{border-color:var(--brand);color:var(--brand);box-shadow:0 8px 20px rgba(255,107,53,.16)}
.badge{display:inline-flex;align-items:center;gap:8px;background:var(--brand-soft);color:var(--brand-ink);border:1px solid var(--brand-line);padding:7px 12px;border-radius:999px;font-size:13px;font-weight:800}
.badge:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--brand);box-shadow:0 0 0 3px rgba(255,107,53,.18)}
.badge.fail{background:var(--bad-soft);color:var(--bad);border-color:var(--bad-line)}.badge.fail:before{background:var(--bad);box-shadow:0 0 0 3px rgba(201,42,47,.15)}
.form-grid{display:grid;grid-template-columns:180px 1fr;gap:18px}label{display:block;color:var(--ink-2);font-size:13px;font-weight:700;margin-bottom:8px}
select,input{width:100%;background:#fff;color:var(--ink);border:1px solid var(--line-2);border-radius:12px;padding:14px;font-size:16px;outline:none;transition:.16s}
select:hover,input:hover{border-color:var(--brand-line)}select:focus,input:focus{border-color:var(--brand);box-shadow:0 0 0 4px rgba(255,107,53,.13)}
.primary{margin-top:22px;background:var(--brand);color:#fff;border:0;border-radius:12px;padding:15px 24px;font-size:16px;font-weight:900;cursor:pointer;box-shadow:0 12px 26px rgba(255,107,53,.28);transition:.16s}
.primary:hover{background:var(--brand-ink);box-shadow:0 14px 30px rgba(255,107,53,.34);transform:translateY(-1px)}
.primary:disabled{opacity:.6;cursor:wait;transform:none;box-shadow:none}
.feature-row{display:flex;flex-wrap:wrap;gap:9px;margin-top:24px}.feature{color:var(--brand-ink);background:var(--surface-2);border:1px solid var(--brand-line);border-radius:999px;padding:7px 12px;font-size:12px;font-weight:700}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:26px 0}
.card{padding:20px;transition:transform .18s,box-shadow .18s}.metrics>.card:hover{transform:translateY(-2px);box-shadow:0 20px 40px rgba(22,32,43,.1)}
.metric-label{color:var(--muted);font-size:13px;font-weight:700}.metric-value{font-size:27px;font-weight:900;margin-top:8px;letter-spacing:-.03em}.metric-value.small{font-size:19px}.metric-sub{color:var(--muted);font-size:11px;margin-top:6px;line-height:1.55}
.tabs{display:flex;gap:6px;padding:6px;background:#fff;border:1px solid var(--line);border-radius:15px;margin:0 0 18px;overflow:auto;box-shadow:0 10px 26px rgba(22,32,43,.05)}
.tab{border:0;background:transparent;color:var(--muted);border-radius:10px;padding:11px 17px;font-size:14px;font-weight:800;cursor:pointer;white-space:nowrap;transition:.16s}
.tab:hover{background:var(--surface);color:var(--ink)}
.tab[aria-selected="true"]{background:var(--brand);color:#fff;box-shadow:0 8px 18px rgba(255,107,53,.26)}
.tab[aria-selected="true"] .muted{color:#fff;opacity:.82}.tab-panel[hidden]{display:none}.tab-panel{animation:fade .22s ease}
@keyframes fade{from{opacity:.35;transform:translateY(4px)}to{opacity:1;transform:none}}
.report-head{display:flex;align-items:center;justify-content:space-between;gap:18px;padding-bottom:22px;border-bottom:1px solid var(--line)}
.report-kicker{color:var(--brand);font-size:11px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}
.report-meta{display:flex;gap:8px;flex-wrap:wrap}.meta-chip{padding:6px 10px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--ink-2);font-size:11px;font-weight:700}
.confidence-track{height:8px;background:#ffe6d8;border-radius:999px;margin-top:13px;overflow:hidden}
.confidence-fill{height:100%;background:linear-gradient(90deg,var(--brand),var(--brand-2));border-radius:inherit}
.confidence-note{color:var(--muted);font-size:11px;margin-top:9px}
.report-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.insight-block{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px}
.insight-block.accent{background:var(--surface-2);border-color:var(--brand-line)}
.section-tag{display:inline-block;color:var(--muted);font-size:11px;font-weight:900;letter-spacing:.11em;text-transform:uppercase;margin-bottom:8px}
.insight-block h3{margin:0 0 14px;font-size:18px;letter-spacing:-.01em}
.insight-list{list-style:none;padding:0;margin:0;display:grid;gap:11px}.insight-list li{position:relative;padding-left:20px;color:var(--ink-2);line-height:1.7}
.insight-list li:before{content:"";position:absolute;left:0;top:.68em;width:7px;height:7px;border-radius:50%;background:var(--brand);box-shadow:0 0 0 3px rgba(255,107,53,.14)}
.insight-list.numbered{counter-reset:item}.insight-list.numbered li{counter-increment:item;padding-left:31px}
.insight-list.numbered li:before{content:counter(item);top:.02em;width:22px;height:22px;display:grid;place-items:center;background:var(--brand-soft);border:1px solid var(--brand-line);box-shadow:none;color:var(--brand-ink);font-size:10px;font-weight:900}
.conclusion{position:relative;overflow:hidden;margin:16px 0;padding:24px 24px 24px 28px;border-radius:16px;background:linear-gradient(135deg,#fff1e8,#fffaf7);border:1px solid var(--brand-line)}
.conclusion:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:linear-gradient(180deg,var(--brand),var(--brand-2))}
.conclusion h3{font-size:20px;margin:7px 0 10px}.conclusion p{font-size:16px;line-height:1.85;color:var(--ink);margin:0}
.conclusion p+p{margin-top:14px}
.risk-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.observation{margin-top:16px}
.disclaimer{display:flex;gap:10px;align-items:flex-start;margin-top:22px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.65}.disclaimer strong{color:var(--ink-2);white-space:nowrap}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--muted);font-weight:800;background:var(--surface)}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:var(--surface)}
.type-pill{display:inline-block;padding:4px 9px;border-radius:999px;background:var(--surface-2);border:1px solid var(--brand-line);color:var(--brand-ink);font-weight:700}.score{color:var(--ok);font-weight:800}
.stance-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:0 0 18px;padding:16px 18px;border-radius:14px;background:#fff;border:1px solid var(--line)}
.stance{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;min-width:104px;padding:10px 18px;border-radius:12px;background:var(--line);color:var(--ink);font-size:22px;font-weight:950;letter-spacing:-.02em;line-height:1.15}
.stance em{font-style:normal;font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;opacity:.72;margin-top:3px}
.stance.up{background:var(--ok-soft);color:var(--ok);border:1px solid var(--ok-line)}.stance.down{background:var(--bad-soft);color:var(--bad);border:1px solid var(--bad-line)}
.stance-basis{flex:1;min-width:240px;line-height:1.6}.stance-basis strong{font-size:15px}
td.pos,.up{color:var(--ok);font-weight:800}td.neg,.down{color:var(--bad);font-weight:800}
.tabs a.tab{text-decoration:none;display:inline-flex;align-items:center}
.lede{margin-top:6px;padding-left:11px;border-left:2px solid var(--brand-line);color:var(--ink-2);font-size:13px;line-height:1.7}
.finding{background:#fff;border:1px solid var(--line);border-left:3px solid var(--line-2);border-radius:12px;padding:13px 15px;margin-bottom:10px}
.finding.high{border-left-color:var(--bad)}.finding.medium{border-left-color:var(--warn)}.finding.low{border-left-color:var(--ok)}
.finding-head{display:flex;align-items:center;gap:9px;margin-bottom:6px;font-size:14px}
.finding .sev{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:7px;background:var(--line);color:var(--ink-2);font-size:11px;font-weight:900}
.finding.high .sev{background:var(--bad-soft);color:var(--bad)}.finding.medium .sev{background:var(--warn-soft);color:var(--warn)}.finding.low .sev{background:var(--ok-soft);color:var(--ok)}
.finding-issue{color:var(--ink-2);line-height:1.75;font-size:14px}
.digest{display:grid;gap:14px}.digest-item{background:#fff;border:1px solid var(--line);border-left:3px solid var(--brand-line);border-radius:12px;padding:15px 17px}
.digest-item h4{margin:0 0 8px;font-size:15px;font-weight:900;letter-spacing:-.01em}.digest-item p{margin:0;color:var(--ink-2);line-height:1.85;font-size:14px}
.digest-takeaway{margin:0 0 14px;padding:13px 16px;border-radius:12px;background:var(--surface-2);border:1px solid var(--brand-line);color:var(--brand-ink);font-weight:800;line-height:1.7;font-size:14px}
.delta{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:999px;border:1px solid var(--line-2);background:#fff;color:var(--ink-2);font-size:11px;font-weight:800;white-space:nowrap}
.delta.up,.delta.down{border-color:var(--brand-line);color:var(--brand-ink);background:var(--brand-soft)}.delta.none{color:var(--muted)}
.tag{display:inline-block;margin-left:5px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:800;white-space:nowrap;vertical-align:text-bottom}
.tag.pos{background:var(--ok-soft);color:var(--ok);border:1px solid var(--ok-line)}.tag.neg{background:var(--bad-soft);color:var(--bad);border:1px solid var(--bad-line)}
.run-summary{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:center;padding:22px;border:1px solid var(--brand-line);background:linear-gradient(135deg,#fff2e9,#fffaf7);border-radius:15px;margin-bottom:18px}
.run-time{font-size:26px;font-weight:900;color:var(--brand-ink);letter-spacing:-.03em}
.steps{display:grid;gap:9px}.step{display:grid;grid-template-columns:32px 1fr auto;gap:12px;align-items:center;padding:14px 16px;border:1px solid var(--line);background:#fff;border-radius:13px;transition:.16s}
.step:hover{border-color:var(--brand-line);box-shadow:0 10px 22px rgba(22,32,43,.06)}
.step-index{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:var(--brand-soft);border:1px solid var(--brand-line);color:var(--brand-ink);font-weight:900;font-size:13px}
.step-name{font-weight:800}.step-meta{font-size:12px;color:var(--muted);margin-top:3px}.status{color:var(--ok);font-size:12px;font-weight:900;text-transform:uppercase}
code{background:var(--surface-2);border:1px solid var(--brand-line);border-radius:6px;padding:1px 6px;font-size:.92em;color:var(--brand-ink)}
details{margin-top:18px}summary{cursor:pointer;color:var(--muted);font-weight:700}summary:hover{color:var(--brand)}
pre.raw{white-space:pre-wrap;overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;color:var(--ink-2);font-size:12px;line-height:1.6}
.loading{display:none;position:fixed;inset:0;z-index:10;background:rgba(255,250,246,.88);backdrop-filter:blur(8px);place-items:center;text-align:center}.loading.show{display:grid}
.spinner{width:48px;height:48px;margin:0 auto 18px;border:4px solid #ffe0cd;border-top-color:var(--brand);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.loading-title{font-size:21px;font-weight:900}.loading-copy{color:var(--muted);margin-top:8px}
.chart-wrap{margin-top:4px}.chart-caption{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:8px}
.ichart{position:relative;width:100%;margin-top:4px;cursor:crosshair}.ichart svg{display:block;width:100%;height:100%}
.ichart-line,.ichart-dot,.ichart-tip{position:absolute;opacity:0;pointer-events:none;transition:opacity .12s ease}
.ichart-line{top:0;bottom:0;width:1px;margin-left:-.5px;background:#e0cec1}
.ichart-dot{width:9px;height:9px;margin:-4.5px 0 0 -4.5px;border-radius:50%;background:var(--brand);box-shadow:0 0 0 4px rgba(255,107,53,.18)}
.ichart-tip{left:0;top:0;z-index:3;background:#fff;border:1px solid var(--line-2);border-radius:11px;padding:8px 11px;font-size:12px;line-height:1.45;color:var(--ink);white-space:nowrap;box-shadow:0 14px 30px rgba(22,32,43,.14)}
.ichart-tip .t-label{display:block;color:var(--muted);font-size:10.5px;margin-bottom:2px}.ichart-tip b{color:var(--brand-ink);font-weight:900;letter-spacing:-.01em}
.ichart.on .ichart-line,.ichart.on .ichart-dot,.ichart.on .ichart-tip{opacity:1}
.sec{margin-bottom:16px}.sec-head{display:flex;align-items:center;gap:13px;margin-bottom:16px}
.sec-num{flex:none;display:grid;place-items:center;width:31px;height:31px;border-radius:11px;background:var(--brand);color:#fff;font-size:14px;font-weight:900;box-shadow:0 8px 16px rgba(255,107,53,.24)}
.sec-title{margin:0;font-size:19px;letter-spacing:-.015em}.sec-sub{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin-top:3px}
.conf-badge{margin-left:auto;display:inline-flex;align-items:center;gap:7px;padding:7px 13px;border-radius:999px;font-size:12px;font-weight:900;white-space:nowrap;background:var(--ok-soft);color:var(--ok);border:1px solid var(--ok-line)}
.conf-badge.mid{background:var(--warn-soft);color:var(--warn);border-color:var(--warn-line)}.conf-badge.low{background:var(--bad-soft);color:var(--bad);border-color:var(--bad-line)}
sup.fn{line-height:0;margin-left:3px}sup.fn a{display:inline-block;min-width:15px;padding:1px 5px;border-radius:6px;background:var(--brand-soft);border:1px solid var(--brand-line);color:var(--brand-ink);font-size:10px;font-weight:900;text-align:center;text-decoration:none;transition:.14s}
sup.fn a:hover{background:var(--brand);border-color:var(--brand);color:#fff}
.src-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.src-item{display:grid;grid-template-columns:30px 1fr;gap:13px;align-items:start;padding:14px 16px;border:1px solid var(--line);background:#fff;border-radius:13px;scroll-margin-top:20px;transition:.16s}
.src-item:hover{border-color:var(--brand-line);box-shadow:0 10px 22px rgba(22,32,43,.06)}
.src-item:target{border-color:var(--brand);box-shadow:0 0 0 4px rgba(255,107,53,.14)}
.src-num{display:grid;place-items:center;height:24px;border-radius:8px;background:var(--brand-soft);border:1px solid var(--brand-line);color:var(--brand-ink);font-size:12px;font-weight:900}
.src-name{font-size:15px;font-weight:800;color:var(--ink)}.src-name:hover{color:var(--brand)}
.src-meta{color:var(--muted);font-size:11px;margin-top:5px;word-break:break-all}
@media(max-width:760px){.wrap{width:min(100% - 28px,1120px);padding-top:32px}.form-grid,.metrics{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.topbar{display:block}.back{display:inline-block;margin-top:16px}.run-summary,.report-grid,.risk-grid{grid-template-columns:1fr}.report-head{align-items:flex-start;flex-direction:column}.step{grid-template-columns:32px 1fr}.status{grid-column:2}.sec-head{flex-wrap:wrap}.conf-badge{margin-left:43px}}
"""


# Vanilla-JS hover layer shared by every interactive chart. Kept outside the page f-strings so the
# braces need no escaping; __ID__ is swapped per chart so several charts never touch each other's DOM.
_CHART_SCRIPT = """<script>(function(){
var root=document.getElementById("__ID__");if(!root)return;
var series;try{series=JSON.parse(root.getAttribute("data-series"))}catch(e){return}
if(!series||!series.length)return;
var line=root.querySelector(".ichart-line"),dot=root.querySelector(".ichart-dot"),tip=root.querySelector(".ichart-tip");
var label=document.createElement("span");label.className="t-label";
var value=document.createElement("b");tip.appendChild(label);tip.appendChild(value);
function xAt(i,width){return series.length<2?width/2:i*width/(series.length-1)}
root.addEventListener("mousemove",function(event){
var rect=root.getBoundingClientRect();if(!rect.width)return;
var index=series.length<2?0:Math.round((event.clientX-rect.left)/rect.width*(series.length-1));
index=Math.max(0,Math.min(series.length-1,index));
var point=series[index],px=xAt(index,rect.width);
root.classList.add("on");
line.style.left=px+"px";dot.style.left=px+"px";dot.style.top=point.y+"px";
label.textContent=point.l;value.textContent=point.v;
var tw=tip.offsetWidth,th=tip.offsetHeight,left=px+14;
if(left+tw>rect.width-4)left=px-tw-14;
if(left<4)left=4;
var top=point.y-th-14;
if(top<4)top=point.y+14;
if(top+th>rect.height-4)top=Math.max(4,rect.height-th-4);
tip.style.left=left+"px";tip.style.top=top+"px";});
root.addEventListener("mouseleave",function(){root.classList.remove("on")});
})();</script>"""


def _format_chart_value(value: float, decimals: int) -> str:
    return f"{float(value):,.{decimals}f}"


def _interactive_chart(chart_id: str, values: list[float], point_labels: list[str], baseline: float | None = None,
                       unit: str = "", height: int = 180, color: str = "#ff6b35") -> str:
    """Single-series line chart with a mouse-tracked crosshair, marker dot and tooltip.

    The SVG is stretched horizontally (preserveAspectRatio="none") so point i always sits at
    fraction i/(n-1) of the container width — that lets the JS overlay map a mouse X back to a
    data index without measuring the SVG. Y positions are precomputed here in container pixels,
    so the browser side does no scaling maths at all. `baseline` draws a dashed reference line."""
    if not values:
        return '<div class="muted">尚無足夠資料繪製圖表</div>'
    pad = 14
    lo, hi = min(values), max(values)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    span = (hi - lo) or 1
    count = len(values)

    def x(i: int) -> float:
        return 500.0 if count < 2 else i * 1000.0 / (count - 1)

    def y(value: float) -> float:
        return pad + (1 - (float(value) - lo) / span) * (height - 2 * pad)

    labels = list(point_labels) + [""] * max(0, count - len(point_labels))
    # One decimal rule per series, so a whole number never renders as "300" beside "301.80".
    decimals = 0 if all(float(value).is_integer() for value in values) else 2
    series = [
        {"l": str(labels[i]), "v": f"{_format_chart_value(value, decimals)}{unit}", "y": round(y(value), 1)}
        for i, value in enumerate(values)
    ]
    points = " ".join(f"{x(i):.2f},{y(value):.1f}" for i, value in enumerate(values))
    baseline_html = ""
    if baseline is not None:
        baseline_y = y(baseline)
        baseline_html = (
            f'<line x1="0" y1="{baseline_y:.1f}" x2="1000" y2="{baseline_y:.1f}" stroke="#dcc9bc" '
            f'stroke-width="1" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>'
        )
    area = f'<polygon points="0,{height} {points} 1000,{height}" fill="{color}" opacity="0.1"/>' if count > 1 else ""
    data = html.escape(json.dumps(series, ensure_ascii=False), quote=True)
    return (
        f'<div class="ichart" id="{chart_id}" style="height:{height}px" data-series="{data}">'
        f'<svg viewBox="0 0 1000 {height}" preserveAspectRatio="none" role="img" aria-label="interactive line chart">'
        f'{baseline_html}{area}'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        f'<div class="ichart-line"></div><div class="ichart-dot" style="background:{color}"></div>'
        f'<div class="ichart-tip"></div></div>'
        + _CHART_SCRIPT.replace("__ID__", chart_id)
    )


_DIGEST_SECTION = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "source_numbers": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["topic", "summary", "source_numbers"],
    "additionalProperties": False,
}
_DIGEST_GROUP = {
    "type": "object",
    "properties": {"takeaway": {"type": "string"}, "sections": {"type": "array", "items": _DIGEST_SECTION}},
    "required": ["takeaway", "sections"],
    "additionalProperties": False,
}
_DIGEST_SCHEMA = {
    "type": "object",
    "properties": {"news": _DIGEST_GROUP, "social": _DIGEST_GROUP, "announcement": _DIGEST_GROUP},
    "required": ["news", "social", "announcement"],
    "additionalProperties": False,
}
_DIGEST_INSTRUCTIONS = (
    "你是加密貨幣市場研究員。以下是針對 {coin} 蒐集到的新聞、社群討論與官方公告，每則都有編號，"
    "並可能附上該則的導言（lede 欄位，為原文開頭 1-3 句）。\n"
    "請用繁體中文，為 news / social / announcement 三組各自產出重點整理：\n"
    "1. sections：依主題分組（每組 2-4 個主題），每個主題的 summary 要 2-4 句、具體說明該主題在講什麼、"
    "牽涉哪些機構或數字、對 {coin} 的意義，不要只是把標題翻譯或複述。\n"
    "2. source_numbers：列出該主題引用到的項目編號。\n"
    "3. takeaway：一句話總結該組整體訊號。\n"
    "資料限制與誠實要求：有 lede 的項目，請優先根據 lede 的實際內容撰寫，可以引用其中出現的數字、"
    "機構名稱與事件；沒有 lede 的項目只有標題，只能陳述標題明確傳達或可合理推得的內容，"
    "並把不確定處寫成推測（例如「標題顯示…，但未說明細節」）。無論如何都不要編造 lede 與標題以外的"
    "數字、引述或事件；也不要假裝讀過全文。若某一組沒有資料，該組 sections 回空陣列、"
    "takeaway 寫「本次無資料」。\n\n"
)


def _summarize_sources(coin: str, groups: dict[str, list[dict]], timeout: int = 45) -> tuple[dict | None, str]:
    """Ask the configured LLM for a Chinese digest of headline-level news/social/announcements.

    Returns (digest, reason). `digest` is None whenever summarisation is unavailable and `reason`
    explains why in Chinese, so the page can fall back to the plain headline list and still tell
    the reader what happened (an exhausted free-tier quota looks nothing like a network failure)."""
    if not llm_is_configured():
        return None, "尚未設定 LLM API Key"
    if not any(groups.values()):
        return None, "本次沒有可摘要的來源"
    prompt = _DIGEST_INSTRUCTIONS.format(coin=coin) + json.dumps(groups, ensure_ascii=False)
    provider = configured_provider()
    try:
        if provider == "gemini":
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": _DIGEST_SCHEMA,
                    "temperature": 0.3,
                },
            }
            endpoint = ("https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{quote(os.getenv('GEMINI_MODEL', 'gemini-3.6-flash'), safe='')}:generateContent")
            request = Request(endpoint, data=json.dumps(payload).encode(), method="POST", headers={
                "x-goog-api-key": os.getenv("GEMINI_API_KEY", ""),
                "Content-Type": "application/json",
                "User-Agent": "hoyabit-market-research-agent/1.0",
            })
            with urlopen(request, timeout=timeout) as response:
                raw = json.loads(response.read().decode())
            parts = (raw.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
        elif provider == "openai":
            payload = {
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "input": prompt,
                "text": {"format": {"type": "json_schema", "name": "source_digest", "strict": True,
                                    "schema": _DIGEST_SCHEMA}},
                "store": False,
            }
            request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                              method="POST", headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
                                                      "Content-Type": "application/json"})
            with urlopen(request, timeout=timeout) as response:
                raw = json.loads(response.read().decode())
            text = raw.get("output_text") or ""
            if not text:
                for item in raw.get("output", []):
                    for content in item.get("content", []):
                        if content.get("type") in {"output_text", "text"}:
                            text = content.get("text", "")
                            break
        else:
            return None, f"未支援的 LLM provider：{provider}"
        digest = json.loads(text)
        if not all(key in digest for key in ("news", "social", "announcement")):
            return None, "模型回應缺少必要欄位"
        return digest, ""
    except HTTPError as error:  # the headline list is always a valid fallback
        if error.code == 429:
            return None, "LLM API 配額已用盡（免費方案每日上限），稍後再試"
        return None, f"LLM API 回應錯誤（HTTP {error.code}）"
    except URLError:
        return None, "無法連線到 LLM API"
    except (ValueError, KeyError, TypeError) as error:
        return None, f"摘要回應解析失敗（{type(error).__name__}）"


# Whale balances arrive as a point-in-time snapshot with no history, so the UI keeps its own small
# ledger of past runs purely to render a change indicator. Presentation-only: nothing here feeds
# back into evidence, indicators or reasoning, and every failure degrades to "no comparison".
WHALE_HISTORY_PATH = Path("outputs-day5/whale_history.json")
WHALE_HISTORY_LIMIT = 30


def _load_whale_history() -> dict:
    try:
        history = json.loads(WHALE_HISTORY_PATH.read_text(encoding="utf-8"))
        return history if isinstance(history, dict) else {}
    except (OSError, ValueError):
        return {}


def _whale_previous_snapshot(coin: str, current_fetched_at: str, labels: list[str]) -> tuple[dict, str]:
    """Return ({wallet label: balance}, fetched_at) for the most recent comparable earlier run.

    Comparable means it shares at least one wallet label, so a snapshot from a different address
    set (fixtures, or a changed curated list) is skipped instead of blocking the comparison."""
    tracked = set(labels)
    for snapshot in reversed(_load_whale_history().get(coin) or []):
        balances = snapshot.get("balances") or {}
        if snapshot.get("fetched_at") != current_fetched_at and tracked & set(balances):
            return balances, str(snapshot.get("fetched_at") or "")
    return {}, ""


def _whale_record_snapshot(coin: str, fetched_at: str, wallets: list[dict]) -> None:
    history = _load_whale_history()
    snapshots = [entry for entry in (history.get(coin) or []) if entry.get("fetched_at") != fetched_at]
    snapshots.append({
        "fetched_at": fetched_at,
        "balances": {wallet["label"]: wallet["balance"] for wallet in wallets},
    })
    history[coin] = snapshots[-WHALE_HISTORY_LIMIT:]
    try:
        WHALE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        WHALE_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _metric(value, suffix: str = "") -> str:
    """Render a metric value, or N/A when the source did not provide one."""
    return "N/A" if value is None else f"{html.escape(str(value))}{suffix}"


def _confidence_level(confidence: float) -> tuple[str, str]:
    """Map a 0~1 confidence into a (Chinese label, CSS modifier) pair."""
    if confidence >= 0.7:
        return "高", ""
    return ("中等", " mid") if confidence >= 0.45 else ("低", " low")


def _home_page() -> str:
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>HOYA BIT 市場研究 Agent</title><style>{BASE_CSS}</style></head><body>
    <main class="wrap"><div class="eyebrow">HOYA BIT · Evidence-first AI</div>
    <h1>加密貨幣市場研究<br><span class="grad">AI Agent</span></h1>
    <p class="lead">輸入幣種與研究問題，系統會整合市場、新聞、鏈上與社群資料，完成證據驗證、指標分析與 AI 推理。</p>
    <section class="panel" style="margin-top:34px"><form method="post" id="research-form">
    <div class="form-grid"><div><label for="coin">研究幣種</label><select id="coin" name="coin">
    <option>BTC</option><option selected>ETH</option><option>SOL</option><option>BNB</option><option>XRP</option>
    </select></div><div><label for="question">研究問題</label>
    <input id="question" name="question" required value="近期市場、新聞與鏈上訊號呈現什麼風險？"></div></div>
    <div class="form-grid" style="margin-top:18px"><div><label for="compare_with">比較幣種（選填）</label>
    <select id="compare_with" name="compare_with">
    <option value="">不進行比較</option>
    <option>BTC</option><option>ETH</option><option>SOL</option><option>BNB</option><option>XRP</option>
    </select></div><div><label>比較模式說明</label>
    <div class="muted" style="font-size:13px;line-height:1.6">選擇第二個幣種後，系統會分別完成兩份完整分析，
    再產出「流動性 / 風險敞口 / 市場關注度」三個維度的並列比較。兩者共用同一組時間預算。</div></div></div>
    <button class="primary" type="submit">開始研究分析</button></form>
    <div class="feature-row"><span class="feature">Evidence ID 可追溯</span><span class="feature">九類資料來源</span>
    <span class="feature">總經與官方公告</span><span class="feature">雙幣比較</span>
    <span class="feature">API 失敗 fallback</span><span class="feature">逾時 watchdog</span>
    <span class="feature">非投資建議</span></div></section>
    <section class="panel" style="margin-top:18px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap">
    <div><div class="report-kicker">Strategy backtest</div>
    <h2 style="margin:7px 0 6px">Vegas 通道策略的歷史表現</h2>
    <div class="muted" style="font-size:14px;line-height:1.7">用 5 年日線資料逐根重放進出場規則，
    與買進持有並列比較，並誠實標示樣本數限制。</div></div>
    <a class="back" href="/backtest?coin=ETH">查看回測結果 →</a></section></main>
    <div class="loading" id="loading" role="status"><div><div class="spinner"></div>
    <div class="loading-title">Agent 正在研究市場</div><div class="loading-copy">六個領域 Agent 平行蒐集資料並爬取新聞全文，接著整理成報告，最後由 Critic 獨立稽核。通常約 30–60 秒。</div></div></div>
    <script>document.getElementById("research-form").addEventListener("submit",function(){{
    document.getElementById("loading").classList.add("show");this.querySelector("button").disabled=true;}});</script></body></html>"""


def _result_page(result: dict, report: str, evidence: list[dict], execution: dict) -> str:
    metric = _metric

    def clock(value) -> str:
        """Trim an ISO timestamp to minutes so tooltips, tables and captions stay readable.

        Returns raw text: chart labels are JSON-encoded (escaped by the caller) and HTML uses
        escape at the point of use, so escaping here would double-encode."""
        return str(value)[:16].replace("T", " ")

    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    # Footnote numbering is the position in the evidence list, so one evidence_id keeps the same
    # number everywhere on the page and always points at that entry of the source list.
    footnote_number = {item["evidence_id"]: index for index, item in enumerate(evidence, 1)}
    id_alternation = "|".join(re.escape(eid) for eid in sorted(evidence_by_id, key=len, reverse=True))
    citation_pattern = re.compile(r"\[?(" + id_alternation + r")\]?") if evidence_by_id else None
    # Facts often arrive as "[EV-MARKET-001]: 已取得…"; a footnote marker before the text would
    # leave a dangling colon, so a leading citation is moved to the end of the sentence instead.
    leading_citation = re.compile(r"^\[?(" + id_alternation + r")\]?\s*[:：]\s*") if evidence_by_id else None

    def footnote(evidence_id: str) -> str:
        item = evidence_by_id[evidence_id]
        return (
            f'<sup class="fn"><a href="#src-{footnote_number[evidence_id]}" '
            f'title="{html.escape(evidence_id)}：{html.escape(item["source"])}">'
            f'{footnote_number[evidence_id]}</a></sup>'
        )

    def linkify(text: str) -> str:
        """Turn inline evidence ids into superscript footnotes pointing at the source list."""
        raw = str(text).strip()
        if citation_pattern is None:
            return html.escape(raw)
        moved = leading_citation.match(raw)
        if moved:
            raw = raw[moved.end():]
        body = citation_pattern.sub(lambda m: footnote(m.group(1)), html.escape(raw))
        return body + footnote(moved.group(1)) if moved else body

    source_items = "".join(
        f'<li class="src-item" id="src-{index}"><span class="src-num">{index}</span><div>'
        f'<a class="src-name" href="{html.escape(item["source_url"], quote=True)}" target="_blank" rel="noopener">'
        f'{html.escape(item["source"])} ↗</a>'
        f'<div class="src-meta">{html.escape(item["evidence_id"])} · {html.escape(item["data_type"])}'
        f' · 取得於 {html.escape(clock(item["fetched_at"]))} · 可靠度 {item["reliability_score"]}</div>'
        f'<div class="src-meta">{html.escape(item["source_url"])}</div></div></li>'
        for index, item in enumerate(evidence, 1)
    ) or '<li class="src-item"><span class="src-num">–</span><div>本次沒有可引用的來源。</div></li>'

    rows = "".join(
        "<tr>"
        f"<td><strong>{html.escape(item['evidence_id'])}</strong></td>"
        f"<td><a href=\"{html.escape(item['source_url'], quote=True)}\" target=\"_blank\" rel=\"noopener\">{html.escape(item['source'])}</a></td>"
        f"<td><span class=\"type-pill\">{html.escape(item['data_type'])}</span></td>"
        f"<td>{html.escape(clock(item['fetched_at']))}</td>"
        f"<td class=\"score\">{item['reliability_score']}</td></tr>"
        for item in evidence
    )
    step_labels = {
        "parse_input": "解析研究問題",
        "collect_evidence": "蒐集多來源 Evidence",
        "calculate_indicators": "計算市場指標",
        "validate_evidence": "驗證證據完整性",
        "llm_reasoning": "Gemini 結構化推理",
        "critic_review": "Critic 獨立稽核",
        "generate_report": "產生研究報告",
    }
    steps = "".join(
        f"""<div class="step"><div class="step-index">{index}</div><div>
        <div class="step-name">{step_labels.get(step['name'], html.escape(step['name']))}</div>
        <div class="step-meta">{html.escape(str(step.get('provider', '')))} {html.escape(str(step.get('model', '')))}</div>
        </div><div class="status">{html.escape(step['status'])}</div></div>"""
        for index, step in enumerate(execution["steps"], 1)
    )
    llm_step = next((step for step in execution["steps"] if step["name"] == "llm_reasoning"), {})
    time_budget = execution.get("time_budget") or {}
    pipeline_clean = not any(
        "fallback" in entry or "skipped" in entry for entry in (execution.get("collection") or [])
    ) and str(llm_step.get("status", "")).startswith(("success", "offline_fallback"))
    derivatives_item = next((item for item in evidence if item["data_type"] == "derivatives"), None)
    funding_bias_labels = {"long_crowded": "多方付費(偏多擁擠)", "short_crowded": "空方付費(偏空擁擠)", "balanced": "多空平衡", "unknown": "資料不可用"}
    funding_rate = derivatives_item["content"].get("funding_rate_pct") if derivatives_item else None
    funding_bias = funding_bias_labels.get(derivatives_item["content"].get("bias") if derivatives_item else None, "N/A")
    whale_item = next((item for item in evidence if item["data_type"] == "whale"), None)
    whale_wallets = (whale_item["content"].get("wallets") if whale_item else None) or []
    previous_balances, previous_seen_at = _whale_previous_snapshot(
        result["coin"],
        whale_item["fetched_at"] if whale_item else "",
        [wallet["label"] for wallet in whale_wallets],
    )
    if whale_item and whale_wallets:
        _whale_record_snapshot(result["coin"], whale_item["fetched_at"], whale_wallets)

    def whale_delta_html(wallet: dict) -> str:
        previous = previous_balances.get(wallet["label"])
        if previous is None:
            return '<span class="delta none">首次紀錄</span>'
        change = wallet["balance"] - previous
        if abs(change) < 1e-9:
            return '<span class="delta none">＝ 與前次持平</span>'
        percent = (change / previous * 100) if previous else 0.0
        arrow = "▲" if change > 0 else "▼"
        return (
            f'<span class="delta {"up" if change > 0 else "down"}">{arrow} {change:+,.4f} '
            f'（{percent:+.2f}%）</span>'
        )

    whale_rows = "".join(
        f'<li><a href="{html.escape(wallet["explorer_url"], quote=True)}" target="_blank" rel="noopener">{html.escape(wallet["label"])}</a>：'
        f'{wallet["balance"]:,} {html.escape(result["coin"])} {whale_delta_html(wallet)}</li>'
        for wallet in whale_wallets
    ) or "<li>此幣種尚未支援巨鯨錢包追蹤，或本次資料暫時無法取得。</li>"
    whale_compare_note = (
        f"與 {html.escape(clock(previous_seen_at))} 的前次查詢比較"
        if previous_seen_at and any(wallet["label"] in previous_balances for wallet in whale_wallets)
        else "本次為第一筆紀錄，下次查詢起會顯示餘額增減"
    )
    vegas_item = next((item for item in evidence if item["data_type"] == "vegas_channel"), None)
    vegas_trend_labels = {"bullish_aligned": "多頭排列", "bearish_aligned": "空頭排列", "mixed": "訊號不一"}
    vegas_signal_labels = {"bull_entry": "多頭進場", "bear_entry": "空頭進場", "take_profit_bull": "多頭停利", "take_profit_bear": "空頭停利"}

    def vegas_timeframe_html(label: str, state: dict | None) -> str:
        if not state:
            return f"<li><strong>{html.escape(label)}</strong>：資料暫時無法取得</li>"
        signal_text = vegas_signal_labels.get(state.get("last_signal"))
        signal_part = f"，最近訊號：{signal_text}（{state.get('bars_since_signal')} 根K棒前）" if signal_text else "，近期無明確進出場訊號"
        return (
            f"<li><strong>{html.escape(label)}</strong>：{vegas_trend_labels.get(state.get('trend'), '不明')}"
            f"，RSI(6) {metric(state.get('rsi'))}"
            f"，{'在通道內' if state.get('in_tunnel') else '在通道外'}{signal_part}</li>"
        )

    vegas_alignment = vegas_item["content"].get("alignment") if vegas_item else None
    vegas_html = (
        vegas_timeframe_html("4H 趨勢", vegas_item["content"].get("4h") if vegas_item else None)
        + vegas_timeframe_html("1H 執行", vegas_item["content"].get("1h") if vegas_item else None)
    )

    news_item = next((item for item in evidence if item["data_type"] == "news"), None)
    news_articles = (news_item["content"].get("items") if news_item else None) or []
    def feed_row(entry: dict) -> str:
        lede = entry.get("summary") or ""
        publisher = entry.get("publisher") or ""
        meta = "　·　".join(part for part in (publisher, entry.get("published", "")) if part)
        return (
            f'<li><a href="{html.escape(entry["url"], quote=True)}" target="_blank" rel="noopener">{html.escape(entry["title"])}</a>'
            f'<div class="src-meta">{html.escape(meta)}</div>'
            + (f'<div class="lede">{html.escape(lede)}</div>' if lede else "")
            + "</li>"
        )

    news_rows = "".join(
        feed_row(article) for article in news_articles if article.get("url")
    ) or "<li>暫無新聞資料。</li>"

    social_item = next((item for item in evidence if item["data_type"] == "social"), None)
    social_posts = (social_item["content"].get("posts") if social_item else None) or []

    def social_row(post: dict) -> str:
        text = (post.get("title") or post.get("text") or "")[:100]
        url = post.get("url", "")
        link = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(text)}</a>' if url else html.escape(text)
        tags = "".join(f'<span class="tag pos">{html.escape(word)}</span>' for word in post.get("matched_positive") or [])
        tags += "".join(f'<span class="tag neg">{html.escape(word)}</span>' for word in post.get("matched_negative") or [])
        return f"<li>{link}{tags}</li>"

    social_rows = "".join(social_row(post) for post in social_posts) or "<li>暫無社群資料。</li>"

    lsratio_item = next((item for item in evidence if item["data_type"] == "long_short_ratio"), None)
    lsratio_content = lsratio_item["content"] if lsratio_item else {}
    lsratio_history = lsratio_content.get("history") or []
    lsratio_current = lsratio_content.get("current")
    lsratio_bias_labels = {"long_dominant": "多方持倉佔優", "short_dominant": "空方持倉佔優", "balanced": "多空接近平衡", "unknown": "資料不可用"}
    lsratio_chart = _interactive_chart(
        "chart-lsratio",
        [point["long_pct"] for point in lsratio_history],
        [clock(point["time"]) for point in lsratio_history],
        baseline=50.0, unit="%",
    )

    macro_content = ((next((item for item in evidence if item["data_type"] == "macro"), None) or {}).get("content") or {})
    fng_history = macro_content.get("fear_greed_history") or []
    fng_chart = _interactive_chart(
        "chart-fng",
        [point["value"] for point in fng_history],
        [point["time"] for point in fng_history],
        baseline=50.0, height=160,
    )
    fng_direction_labels = {
        "risk_appetite_improving": "風險偏好回升", "risk_appetite_deteriorating": "風險偏好轉弱",
        "risk_appetite_stable": "風險偏好持平", "unknown": "資料不可用",
    }
    fed_releases = macro_content.get("fed_releases") or []
    fed_rows = "".join(
        f'<li><a href="{html.escape(entry["url"], quote=True)}" target="_blank" rel="noopener">{html.escape(entry["title"])}</a>'
        f'<span class="muted"> — {html.escape(entry.get("published", ""))}</span></li>'
        for entry in fed_releases if entry.get("url")
    ) or "<li>聯準會政策發布本次不可用。</li>"

    announcement_item = next((item for item in evidence if item["data_type"] == "announcement"), None)
    announcement_content = (announcement_item or {}).get("content") or {}
    announcement_rows = "".join(
        feed_row(entry) for entry in (announcement_content.get("items") or []) if entry.get("url")
    ) or "<li>官方公告來源本次不可用。</li>"

    # Headlines are all the collectors store (no article bodies), so the digest reorganises and
    # explains them in Chinese rather than inventing detail. Numbering is per group and drives the
    # trailing source markers, keeping the prose free of inline links.
    digest_sources = {
        "news": [
            {"n": index, "title": article["title"], "published": article.get("published", ""),
             "url": article["url"], "lede": article.get("summary", "")}
            for index, article in enumerate((a for a in news_articles if a.get("url")), 1)
        ],
        "social": [
            {"n": index, "title": (post.get("title") or post.get("text") or "")[:200],
             "published": post.get("published", ""), "url": post.get("url", ""), "lede": ""}
            for index, post in enumerate(social_posts, 1)
        ],
        "announcement": [
            {"n": index, "title": entry["title"], "published": entry.get("published", ""),
             "url": entry["url"], "lede": entry.get("summary", "")}
            for index, entry in enumerate((e for e in (announcement_content.get("items") or []) if e.get("url")), 1)
        ],
    }
    digest, digest_error = _summarize_sources(result["coin"], {
        group: [
            {key: value for key, value in item.items() if key != "url" and (key != "lede" or value)}
            for item in items
        ]
        for group, items in digest_sources.items()
    })
    lede_count = sum(1 for item in digest_sources["news"] + digest_sources["announcement"] if item["lede"])

    def digest_ref(group: str, number) -> str:
        item = next((entry for entry in digest_sources[group] if entry["n"] == number), None)
        if not item:
            return ""
        if not item["url"]:
            return f'<sup class="fn"><span title="{html.escape(item["title"])}">{item["n"]}</span></sup>'
        return (
            f'<sup class="fn"><a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener" '
            f'title="{html.escape(item["title"])}">{item["n"]}</a></sup>'
        )

    def digest_html(group: str, fallback_rows: str, empty_text: str) -> str:
        items = digest_sources[group]
        if not items:
            return f'<div class="muted">{empty_text}</div>'
        sections = ((digest or {}).get(group) or {}).get("sections") or []
        raw_list = (
            f'<details><summary>查看原始標題與連結（{len(items)} 則）</summary>'
            f'<ul class="insight-list" style="margin-top:12px">{fallback_rows}</ul></details>'
        )
        if not sections:
            reason = digest_error or "模型本次未產出此分組的摘要"
            return (
                f'<div class="muted" style="margin-bottom:10px">摘要暫時無法產生：{html.escape(reason)}。以下顯示原始標題。</div>'
                f'<ul class="insight-list">{fallback_rows}</ul>'
            )
        takeaway = ((digest or {}).get(group) or {}).get("takeaway") or ""
        blocks = "".join(
            f'<div class="digest-item"><h4>{html.escape(str(section.get("topic", "")))}</h4>'
            f'<p>{html.escape(str(section.get("summary", "")))}'
            f'{"".join(digest_ref(group, number) for number in section.get("source_numbers") or [])}</p></div>'
            for section in sections
        )
        takeaway_html = f'<p class="digest-takeaway">{html.escape(takeaway)}</p>' if takeaway else ""
        return f'{takeaway_html}<div class="digest">{blocks}</div>{raw_list}'

    news_digest = digest_html("news", news_rows, "本次沒有取得新聞資料。")
    social_digest = digest_html("social", social_rows, "本次沒有取得社群資料。")
    announcement_digest = digest_html("announcement", announcement_rows, "官方公告來源本次不可用。")
    digest_note = (
        f"摘要由 AI 依據各則的標題與導言整理，本次 {lede_count} 則附有原文導言（媒體 feed 提供的前 1-3 句），"
        "其餘僅有標題。導言不等於全文，數字與細節請點開原文確認。"
    )

    critique = result.get("critique") or {}
    critic_step = next((step for step in execution["steps"] if step["name"] == "critic_review"), {})
    verdict_labels = {"pass": ("通過", ""), "concerns": ("有需注意之處", " mid"), "fail": ("結論不被證據支撐", " low")}
    verdict_text, verdict_class = verdict_labels.get(critique.get("verdict"), ("未執行", " mid"))
    severity_labels = {"high": "高", "medium": "中", "low": "低"}
    critic_findings = "".join(
        f'<div class="finding {html.escape(str(finding.get("severity", "low")))}">'
        f'<div class="finding-head"><span class="sev">{severity_labels.get(finding.get("severity"), "低")}</span>'
        f'<strong>{html.escape(str(finding.get("category", "")))}</strong>'
        f'{linkify(str(finding.get("evidence_id", "")))}</div>'
        f'<div class="finding-issue">{html.escape(str(finding.get("issue", "")))}</div>'
        f'<div class="src-meta">針對：{html.escape(str(finding.get("claim", ""))[:120])}</div></div>'
        for finding in critique.get("findings") or []
    ) or '<div class="muted">稽核未發現實質問題。</div>'

    agents_report = execution.get("collection_agents") or []
    agent_rows = "".join(
        f'<tr><td><strong>{html.escape(str(agent.get("label", "")))}</strong>'
        f'<div class="src-meta">{html.escape(str(agent.get("agent", "")))}</div></td>'
        f'<td>{html.escape("、".join(agent.get("sources") or []))}</td>'
        f'<td>{agent.get("duration_ms", 0):,.0f} ms</td>'
        f'<td>{html.escape("、".join(entry.split(":", 1)[1] for entry in agent.get("log") or []))}</td></tr>'
        for agent in sorted(agents_report, key=lambda item: -item.get("duration_ms", 0))
    )
    phase_ceilings = (execution.get("time_budget") or {}).get("phase_ceilings_seconds") or {}
    phase_actual = (execution.get("time_budget") or {}).get("phase_actual_ms") or {}
    phase_names = {"collection": "① 蒐集資料（含全文爬取）", "reasoning": "② 整理資料與產出報告", "critic": "③ Critic 稽核"}
    phase_rows = "".join(
        f'<tr><td><strong>{html.escape(name)}</strong></td>'
        f'<td>{phase_ceilings.get(key, 0) / 60:.0f} 分鐘</td>'
        f'<td>{phase_actual.get(key + "_ms", 0) / 1000:.1f} 秒</td>'
        f'<td>{(phase_actual.get(key + "_ms", 0) / 1000) / phase_ceilings.get(key, 1) * 100:.1f}%</td></tr>'
        for key, name in phase_names.items()
    )
    news_fulltext_count = ((next((item for item in evidence if item["data_type"] == "news"), None) or {})
                           .get("content") or {}).get("fulltext_count", 0)

    stance = result.get("stance") or {}
    stance_class = {"bullish": " up", "bearish": " down"}.get(stance.get("stance"), "")
    stance_chip = (
        f'<span class="stance{stance_class}">{html.escape(str(stance.get("label", "N/A")))}'
        f'<em>{html.escape(str(stance.get("label_en", "")))}</em></span>'
        if stance else ""
    )
    stance_drivers = "".join(
        f'<li>{linkify(driver["text"] + "（" + driver["evidence_id"] + "）")}'
        f'<span class="muted">　權重 {driver["weight"]}</span></li>'
        for driver in stance.get("drivers") or []
    ) or "<li>本次沒有單一方向的主要推力，判讀維持中性。</li>"

    history_item = next((item for item in evidence if item["data_type"] == "price_history"), None)
    history_content = (history_item or {}).get("content") or {}
    history_windows = history_content.get("windows") or {}
    window_labels = {"14d": "近 14 天", "90d": "近 90 天", "365d": "近 1 年", "full": "全期間"}
    history_rows = "".join(
        f'<tr><td><strong>{html.escape(window_labels.get(label, label))}</strong>'
        f'<div class="src-meta">{html.escape(str(window["date_start"]))} → {html.escape(str(window["date_end"]))}'
        f'（{window["days"]} 天）</div></td>'
        f'<td class="{"pos" if (window.get("return_pct") or 0) > 0 else "neg"}">{metric(window.get("return_pct"), "%")}</td>'
        f'<td>{metric(window.get("volatility_annualised_pct"), "%")}</td>'
        f'<td class="neg">{metric(window.get("max_drawdown_pct"), "%")}</td>'
        f'<td>{metric(window.get("percentile_in_range"), "%")}</td></tr>'
        for label, window in history_windows.items()
    )
    history_chart = _interactive_chart(
        "chart-history", history_content.get("prices") or [], history_content.get("dates") or [],
        unit=" USD", height=200,
    )
    history_caption = (
        f"{html.escape(str(history_content.get('date_start', '')))} → {html.escape(str(history_content.get('date_end', '')))}"
        f"（{history_content.get('rows', 0)} 個交易日，圖表等距取樣 {len(history_content.get('prices') or [])} 點）"
        if history_content else "未載入長期歷史資料"
    )

    tvl_item = next((item for item in evidence if item["data_type"] == "tvl"), None)
    tvl_content = (tvl_item or {}).get("content") or {}
    tvl_history = tvl_content.get("history") or []
    tvl_direction_labels = {"expanding": "鏈上資金流入（擴張）", "contracting": "鏈上資金流出（收縮）", "flat": "大致持平", "unknown": "資料不可用"}
    tvl_chart = _interactive_chart(
        "chart-tvl",
        [round(point["tvl_usd"] / 1e9, 3) for point in tvl_history],
        [point["time"] for point in tvl_history],
        unit=" B USD", height=180,
    )
    tvl_caption = (
        f"{html.escape(str(tvl_history[0]['time']))} → {html.escape(str(tvl_history[-1]['time']))}"
        f"（共 {len(tvl_history)} 天）" if len(tvl_history) > 1 else "資料點不足"
    )

    price_series = ((next((item for item in evidence if item["data_type"] == "market"), None) or {}).get("content") or {})
    price_values = price_series.get("prices") or []
    price_dates = price_series.get("dates") or [f"第 {i + 1} 天" for i in range(len(price_values))]
    price_chart = _interactive_chart("chart-price", price_values, price_dates, unit=" USD", height=220)
    price_caption = f"{html.escape(str(price_dates[0]))} → {html.escape(str(price_dates[-1]))}（共 {len(price_values)} 個資料點）" if len(price_values) > 1 else "資料點不足"
    lsratio_caption = (
        f"{html.escape(clock(lsratio_history[0]['time']))} → {html.escape(clock(lsratio_history[-1]['time']))}"
        f"（共 {len(lsratio_history)} 個資料點）" if len(lsratio_history) > 1 else "資料點不足"
    )
    fng_caption = (
        f"{html.escape(clock(fng_history[0]['time']))} → {html.escape(clock(fng_history[-1]['time']))}"
        f"（共 {len(fng_history)} 個資料點）" if len(fng_history) > 1 else "資料點不足"
    )

    reasoning = result["reasoning"]
    confidence_raw = float(reasoning.get("confidence", 0) or 0)
    confidence = max(0, min(100, round(confidence_raw * 100)))
    confidence_label, confidence_class = _confidence_level(confidence_raw)
    duration_seconds = round(execution.get("duration_ms", 0) / 1000, 1)

    def insight_list(items: list, numbered: bool = False) -> str:
        class_name = "insight-list numbered" if numbered else "insight-list"
        content = "".join(f"<li>{linkify(item)}</li>" for item in items)
        return f'<ul class="{class_name}">{content}</ul>'

    def split_blocks(text: str, min_chars: int = 24, orphan_chars: int = 12) -> list[str]:
        """Break a run-on judgement into readable blocks at sentence and clause enders.

        The model returns everything as one long line. Clauses are accumulated until a block is
        substantial (min_chars) so the split never leaves a two-word fragment; a short tail is
        only kept separate when it can stand alone (orphan_chars), otherwise it joins the previous
        block."""
        clauses = [part.strip() for part in re.findall(r"[^。！？；!?;]+[。！？；!?;]*", str(text)) if part.strip()]
        blocks: list[str] = []
        current = ""
        for clause in clauses:
            current += clause
            if len(current) >= min_chars:
                blocks.append(current)
                current = ""
        if current:
            if blocks and len(current) < orphan_chars:
                blocks[-1] += current
            else:
                blocks.append(current)
        return blocks or [str(text)]

    def paragraphs(text: str) -> str:
        return "".join(f"<p>{linkify(block)}</p>" for block in split_blocks(text))

    # The offline/fallback reasoning path often returns the same sentence for both fields; showing
    # it twice (headline + conclusion box) reads as a bug, so keep only the fuller of the two.
    judgment_text = str(reasoning["market_judgment"]).strip()
    conclusion_text = str(reasoning["conclusion"]).strip()
    squashed = (re.sub(r"\s+", "", judgment_text), re.sub(r"\s+", "", conclusion_text))
    if squashed[0] in squashed[1] or squashed[1] in squashed[0]:
        judgment_html, conclusion_html = "", paragraphs(max((conclusion_text, judgment_text), key=len))
    else:
        judgment_html = (
            f'<h2 style="font-size:22px;line-height:1.6;letter-spacing:-.02em;margin:0 0 16px">{linkify(judgment_text)}</h2>'
        )
        conclusion_html = paragraphs(conclusion_text)

    vegas_1h_state = vegas_item["content"].get("1h") if vegas_item else None
    volume_spike_text = "是（2倍以上）" if vegas_1h_state and vegas_1h_state.get("volume_spike") else "否" if vegas_1h_state else "N/A"

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(result['coin'])} 研究報告</title><style>{BASE_CSS}</style></head><body>
    <main class="wrap"><div class="topbar"><div><span class="badge">分析完成</span>
    <h1 style="font-size:clamp(32px,4vw,48px)">{html.escape(result['coin'])} 市場研究報告</h1>
    <div class="muted">{html.escape(result['question'])}</div></div><a class="back" href="/">＋ 新增研究</a></div>
    <nav class="tabs" role="tablist" aria-label="研究結果分頁" style="margin-top:28px">
    <button class="tab" role="tab" aria-selected="true" aria-controls="overview">研究摘要</button>
    <button class="tab" role="tab" aria-selected="false" aria-controls="charts">互動圖表</button>
    <button class="tab" role="tab" aria-selected="false" aria-controls="evidence">Evidence <span class="muted">({len(evidence)})</span></button>
    <button class="tab" role="tab" aria-selected="false" aria-controls="execution">執行流程</button></nav>
    <section class="panel tab-panel" id="overview" role="tabpanel">
    <div class="report-head"><div><div class="report-kicker">Professional Research Brief</div><h2 style="margin:7px 0 0">完整研究報告</h2></div>
    <div class="report-meta"><span class="meta-chip">{html.escape(result['coin'])}</span><span class="meta-chip">{len(evidence)} 筆 Evidence</span>
    <span class="meta-chip">{duration_seconds} 秒完成</span><span class="meta-chip">{html.escape(str(llm_step.get('model', 'AI reasoning')))}</span></div></div>
    <article class="insight-block sec" style="margin-top:22px">
    <div class="sec-head"><div class="sec-num">1</div><div><h3 class="sec-title">結論</h3><div class="sec-sub">Conclusion</div></div>
    <span class="conf-badge{confidence_class}">信心 {confidence_label}／{confidence}%</span></div>
    <div class="stance-row">{stance_chip}
    <div class="stance-basis"><strong>{html.escape(str(stance.get('basis', '')))}</strong>
    <div class="src-meta">多方權重 {stance.get('bull_weight', 0)}／空方權重 {stance.get('bear_weight', 0)}
    ／共 {stance.get('signal_count', 0)} 項訊號，門檻：淨優勢 25% 以上才給方向</div></div></div>
    {judgment_html}
    <div class="conclusion" style="margin:0">{conclusion_html}</div>
    <details style="margin-top:14px"><summary>這個立場是怎麼算出來的？</summary>
    <ul class="insight-list" style="margin-top:12px">{stance_drivers}</ul>
    <div class="metric-sub" style="margin-top:10px">立場由證據訊號加權計算，非模型自由生成；同一批證據永遠得到同一個立場，離線模式也一致。</div></details>
    <div class="confidence-track" style="margin-top:16px"><div class="confidence-fill" style="width:{confidence}%"></div></div>
    <div class="confidence-note">分析信心度 {confidence}%（{confidence_label}）／依資料一致性與來源可靠度綜合評估</div></article>
    <article class="insight-block sec"><div class="sec-head"><div class="sec-num">2</div>
    <div><h3 class="sec-title">已確認事實</h3><div class="sec-sub">Verified facts</div></div></div>
    {insight_list(reasoning.get('facts', []))}
    <div class="metric-sub" style="margin-top:12px">上標數字為腳註，對應第 6 節「資料來源」清單編號。</div></article>
    <article class="insight-block sec"><div class="sec-head"><div class="sec-num">3</div>
    <div><h3 class="sec-title">推論</h3><div class="sec-sub">Inference</div></div></div>
    {insight_list(reasoning.get('inferences', []), numbered=True)}</article>
    <article class="insight-block sec"><div class="sec-head"><div class="sec-num">4</div>
    <div><h3 class="sec-title">反方證據</h3><div class="sec-sub">Counter evidence</div></div></div>
    {insight_list(reasoning.get('counter_evidence', []))}</article>
    <article class="insight-block sec accent"><div class="sec-head"><div class="sec-num">5</div>
    <div><h3 class="sec-title">信心與限制</h3><div class="sec-sub">Confidence &amp; limitations</div></div>
    <span class="conf-badge{confidence_class}">信心 {confidence_label}／{confidence}%</span></div>
    {insight_list(result.get('risk_factors', []))}
    <div class="section-tag" style="margin-top:18px">後續觀察重點 · Monitoring plan</div>
    {insight_list(reasoning.get('observation_points', []), numbered=True)}</article>
    <article class="insight-block sec"><div class="sec-head"><div class="sec-num">6</div>
    <div><h3 class="sec-title">資料來源</h3><div class="sec-sub">Sources</div></div>
    <span class="meta-chip" style="margin-left:auto">{len(evidence)} 筆</span></div>
    <ul class="src-list">{source_items}</ul></article>
    <article class="insight-block sec accent"><div class="sec-head"><div class="sec-num">7</div>
    <div><h3 class="sec-title">獨立稽核</h3><div class="sec-sub">Critic review</div></div>
    <span class="conf-badge{verdict_class}">{html.escape(verdict_text)}</span></div>
    {f'<p style="margin:0 0 14px;line-height:1.8;color:var(--ink)">{html.escape(str(critique.get("summary", "")))}</p>' if critique else ''}
    {f'<div class="metric-sub" style="margin-bottom:14px">信心經稽核調整：{critique.get("original_confidence")} → <strong>{critique.get("adjusted_confidence")}</strong></div>' if critique else f'<div class="muted" style="margin-bottom:14px">本次未執行稽核（{html.escape(str(critic_step.get("status", "unknown")))}）。稽核為獨立的第二次 LLM 呼叫，配額不足或逾時會自動略過，報告其餘部分不受影響。</div>'}
    {critic_findings if critique else ''}
    <div class="metric-sub" style="margin-top:14px">稽核 agent 只能標註問題與下調信心，<strong>不能改寫結論</strong> ——
    會改寫的稽核等於第二個分析師，讀者就失去了獨立檢查。</div></article>
    <div class="disclaimer"><strong>研究聲明</strong><span>本報告僅供研究與展示用途，不構成投資建議、買賣指令或任何形式的財務承諾。請搭配完整 Evidence 與自身風險承受度獨立判斷。</span></div>
    <details><summary>查看原始 report.md 內容</summary><pre class="raw">{html.escape(report)}</pre></details></section>
    <section class="panel tab-panel" id="charts" role="tabpanel" hidden>
    <div class="report-head"><div><div class="report-kicker">Interactive charts</div><h2 style="margin:7px 0 0">互動圖表與原始訊號</h2></div>
    <div class="report-meta"><span class="meta-chip">滑鼠移入圖表可查看每個資料點</span></div></div>
    <article class="insight-block" style="margin-top:22px"><span class="section-tag">Price trend</span><h3>{html.escape(result['coin'])} 價格走勢</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">資料來源：market evidence 的每日收盤價（USD）</div>
    {price_chart}
    <div class="chart-caption"><span>{price_caption}</span><span>移動滑鼠可顯示該日日期與收盤價</span></div></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Long-horizon context</span><h3>長期價格脈絡（5 年日線）</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">資料來源：本地 5 年日線 CSV（Binance 公開日線，2021-07 起）。
    其餘來源皆為 14 天窗口，這張表用來回答「現在這個波動，以這個幣自己的歷史標準來看算大還算小」。</div>
    {history_chart}
    <div class="chart-caption"><span>{history_caption}</span><span>移動滑鼠可顯示該日日期與收盤價</span></div>
    <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>期間</th><th>報酬</th><th>年化波動</th><th>最大回撤</th><th>價格區間分位</th></tr></thead>
    <tbody>{history_rows}</tbody></table></div>
    <div class="metric-sub" style="margin-top:10px">「價格區間分位」＝目前收盤價落在該期間高低點之間的位置；接近 0% 代表貼近期間低點，接近 100% 代表貼近高點。</div></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Chain fundamentals · TVL</span><h3>鏈上鎖倉量（TVL）</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">資料來源：DefiLlama {html.escape(str(tvl_content.get('chain', 'N/A')))} 鏈 TVL（免金鑰）。
    TVL 反映真實鎖進鏈上的資金，可用來檢驗價格變動有沒有基本面支撐。</div>
    <div class="metrics" style="grid-template-columns:repeat(3,1fr);margin:0 0 14px">
    <div class="card"><div class="metric-label">目前 TVL</div><div class="metric-value">{('$' + format(tvl_content['tvl_usd'] / 1e9, ',.2f') + ' B') if tvl_content.get('tvl_usd') else 'N/A'}</div></div>
    <div class="card"><div class="metric-label">近 30 天變化</div><div class="metric-value">{metric(tvl_content.get('change_30d_pct'), '%')}</div>
    <div class="metric-sub">{html.escape(tvl_direction_labels.get(tvl_content.get('direction'), 'N/A'))}</div></div>
    <div class="card"><div class="metric-label">近 90 天變化</div><div class="metric-value">{metric(tvl_content.get('change_pct'), '%')}</div></div></div>
    {tvl_chart}
    <div class="chart-caption"><span>{tvl_caption}</span><span>移動滑鼠可顯示該日 TVL（十億美元）</span></div></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Long/short position ratio</span><h3>大戶多空持倉比走勢</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">資料來源：Binance 大戶持倉量多空比（非帳戶數）；虛線為 50% 多空中性水位</div>
    <div class="metrics" style="grid-template-columns:repeat(3,1fr);margin:0 0 14px">
    <div class="card"><div class="metric-label">多方佔比</div><div class="metric-value">{metric(lsratio_current.get('long_pct') if lsratio_current else None, '%')}</div></div>
    <div class="card"><div class="metric-label">空方佔比</div><div class="metric-value">{metric(lsratio_current.get('short_pct') if lsratio_current else None, '%')}</div></div>
    <div class="card"><div class="metric-label">判斷可行性</div><div class="metric-value">{metric(lsratio_content.get('consistency_pct'), '%')}</div><div class="metric-sub">近期同方向比例</div></div></div>
    <div class="muted" style="margin-bottom:8px">{html.escape(lsratio_bias_labels.get(lsratio_content.get('bias'), 'N/A'))}</div>
    {lsratio_chart}
    <div class="chart-caption"><span>{lsratio_caption}</span><span>移動滑鼠可顯示該時點與多方佔比 %</span></div></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Vegas Channel + RSI strategy</span><h3>Vegas 通道狀態（4H 趨勢 / 1H 執行）</h3>
    <div style="margin-bottom:14px"><span class="badge{'' if vegas_alignment and vegas_alignment.get('passed') else ' fail'}">{'通過' if vegas_alignment and vegas_alignment.get('passed') else '不通過'}</span>
    <span class="muted" style="margin-left:10px">{html.escape(vegas_alignment.get('note')) if vegas_alignment else '資料暫時無法取得'}</span></div>
    <div class="metrics" style="grid-template-columns:repeat(3,1fr);margin:0 0 14px">
    <div class="card"><div class="metric-label">1H RSI(6)</div><div class="metric-value">{metric(vegas_1h_state.get('rsi') if vegas_1h_state else None)}</div><div class="metric-sub">Vegas 通道執行週期</div></div>
    <div class="card"><div class="metric-label">1H 成交量爆量</div><div class="metric-value small">{volume_spike_text}</div><div class="metric-sub">是否超過均量兩倍</div></div>
    <div class="card"><div class="metric-label">資金費率</div><div class="metric-value">{metric(funding_rate, '%')}</div><div class="metric-sub">{html.escape(funding_bias)}</div></div></div>
    <ul class="insight-list">{vegas_html}</ul>
    <div class="metric-sub" style="margin-top:10px">大小時區方向須一致才「通過」；EMA144~987 通道 + RSI(6) + 成交量脈衝確認，需 1000 根K棒歷史，與其他 14 天指標的資料窗口不同</div></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Macro backdrop</span><h3>總體經濟背景：恐懼貪婪指數與聯準會</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">資料來源：alternative.me（全市場，非單一幣種）；虛線為 50 中性水位</div>
    <div class="metrics" style="grid-template-columns:repeat(2,1fr);margin:0 0 14px">
    <div class="card"><div class="metric-label">恐懼貪婪指數</div><div class="metric-value">{metric(macro_content.get('fear_greed_value'))}</div>
    <div class="metric-sub">{html.escape(str(macro_content.get('fear_greed_classification') or 'N/A'))}</div></div>
    <div class="card"><div class="metric-label">14 天變化</div><div class="metric-value">{metric(macro_content.get('fear_greed_change_14d'))}</div>
    <div class="metric-sub">{html.escape(fng_direction_labels.get(macro_content.get('risk_appetite_direction'), 'N/A'))}</div></div></div>
    {fng_chart}
    <div class="chart-caption"><span>{fng_caption}</span><span>移動滑鼠可顯示該時點與指數值</span></div>
    <div class="section-tag" style="margin-top:16px">Federal Reserve 貨幣政策發布</div>
    <ul class="insight-list">{fed_rows}</ul></article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">News digest</span><h3>消息面重點整理：新聞</h3>
    <div class="muted" style="font-size:13px;margin-bottom:14px">{digest_note}</div>
    {news_digest}</article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Social digest</span><h3>消息面重點整理：社群討論</h3>
    <div class="muted" style="font-size:13px;margin-bottom:14px">來源為公開社群貼文標題，情緒判斷仍以正負向關鍵字計數為準，摘要僅供理解討論在談什麼。</div>
    {social_digest}</article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Official announcements</span><h3>官方公告重點整理</h3>
    <div class="muted" style="font-size:13px;margin-bottom:14px">來源：{html.escape(str((announcement_item or {}).get('source', 'N/A')))}
    ／{'第一方官方 feed' if announcement_content.get('first_party') else '官方網域限定的新聞聚合（非第一方 feed，可靠度較低）'}</div>
    {announcement_digest}</article>
    <article class="insight-block" style="margin-top:16px"><span class="section-tag">Known whale wallets</span><h3>已知大戶錢包餘額</h3>
    <div class="muted" style="font-size:13px;margin-bottom:10px">{whale_compare_note}</div>
    <ul class="insight-list">{whale_rows}</ul>
    <div class="metric-sub" style="margin-top:10px">僅追蹤已知大型交易所/機構地址的即時餘額，非全網即時巨鯨偵測；目前僅支援 BTC/ETH/BNB。
    增減為兩次查詢之間的餘額差，交易所錢包的流入流出成因很多，不等同買賣方向。</div></article></section>
    <section class="panel tab-panel" id="evidence" role="tabpanel" hidden><h2>Evidence 資料清單</h2>
    <div class="table-wrap"><table><thead><tr><th>Evidence ID</th><th>資料來源</th><th>類型</th><th>取得時間</th><th>可靠度</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    <section class="panel tab-panel" id="execution" role="tabpanel" hidden><div class="run-summary"><div>
    <span class="badge{'' if pipeline_clean else ' fail'}">{'Pipeline Success' if pipeline_clean else 'Pipeline Degraded'}</span>
    <h2 style="margin:12px 0 5px">{'所有 Agent 步驟均成功完成' if pipeline_clean else '流程完成，但部分來源以 fallback 或逾時跳過'}</h2>
    <div class="muted">{html.escape(str(llm_step.get('provider', 'LLM')))} · {html.escape(str(llm_step.get('model', '')))}</div>
    <div class="metric-sub">時間預算 {time_budget.get('budget_seconds', 'N/A')} 秒
    ／LLM 判斷時剩餘 {time_budget.get('llm_seconds_remaining_at_decision', 'N/A')} 秒
    ／watchdog 跳過 {len(time_budget.get('sources_skipped_by_watchdog') or [])} 個來源</div></div>
    <div><div class="metric-label">總執行時間</div><div class="run-time">{duration_seconds} 秒</div></div></div>
    <article class="insight-block" style="margin-bottom:16px"><span class="section-tag">Phase budget</span>
    <h3>三階段時間預算</h3>
    <div class="muted" style="font-size:13px;margin-bottom:10px">每個階段做完就進入下一階段，上限只是天花板而非目標。</div>
    <div class="table-wrap"><table><thead><tr><th>階段</th><th>上限</th><th>實際耗時</th><th>用掉比例</th></tr></thead>
    <tbody>{phase_rows}</tbody></table></div></article>
    <article class="insight-block" style="margin-bottom:16px"><span class="section-tag">Parallel collection agents</span>
    <h3>平行蒐集 Agent（{len(agents_report)} 個領域同時執行）</h3>
    <div class="muted" style="font-size:13px;margin-bottom:10px">各 agent 獨立負責一個研究面向並同時執行，
    因此總耗時等於最慢的 agent，而不是所有來源的加總。本次新聞面完成 {news_fulltext_count} 篇全文爬取。</div>
    <div class="table-wrap"><table><thead><tr><th>Agent</th><th>負責來源</th><th>耗時</th><th>結果</th></tr></thead>
    <tbody>{agent_rows}</tbody></table></div></article>
    <div class="muted" style="margin-bottom:14px">來源收集結果：{html.escape("、".join(execution.get("collection") or []))}</div>
    <div class="steps">{steps}</div><details><summary>查看原始 Execution Log</summary>
    <pre class="raw">{html.escape(json.dumps(execution, ensure_ascii=False, indent=2))}</pre></details></section></main>
    <script>const tabs=[...document.querySelectorAll('[role="tab"]')];const panels=[...document.querySelectorAll('[role="tabpanel"]')];
    tabs.forEach(tab=>tab.addEventListener("click",()=>{{tabs.forEach(t=>t.setAttribute("aria-selected","false"));
    panels.forEach(p=>p.hidden=true);tab.setAttribute("aria-selected","true");document.getElementById(tab.getAttribute("aria-controls")).hidden=false;}}));</script>
    </body></html>"""


def _comparison_page(payload: dict) -> str:
    coin_a, coin_b = payload["coins"]
    comparison = payload["comparison"]
    profiles = payload["profiles"]

    def cell(value) -> str:
        if value is None:
            return "N/A"
        return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"

    def dimension(title: str, tag: str, key: str, rows: list[tuple[str, object, object]], basis: str) -> str:
        verdict = comparison[key]["verdict"]
        body = "".join(
            f"<tr><td>{html.escape(label)}</td><td>{cell(value_a)}</td><td>{cell(value_b)}</td></tr>"
            for label, value_a, value_b in rows
        )
        return f"""<article class="insight-block" style="margin-bottom:16px"><span class="section-tag">{html.escape(tag)}</span>
        <h3>{html.escape(title)}</h3>
        <div class="conclusion" style="margin:0 0 14px;padding:16px"><p style="font-size:15px">{html.escape(verdict)}</p></div>
        <div class="table-wrap"><table><thead><tr><th>指標</th><th>{html.escape(coin_a)}</th><th>{html.escape(coin_b)}</th></tr></thead>
        <tbody>{body}</tbody></table></div>
        <div class="metric-sub" style="margin-top:10px">判讀依據：{html.escape(basis)}</div></article>"""

    liquidity_html = dimension("流動性", "Liquidity", "liquidity", [
        ("平均日成交額 (USD)", profiles[coin_a]["liquidity"]["avg_daily_volume_usd"], profiles[coin_b]["liquidity"]["avg_daily_volume_usd"]),
        ("最新成交額 (USD)", profiles[coin_a]["liquidity"]["latest_volume_usd"], profiles[coin_b]["liquidity"]["latest_volume_usd"]),
        ("成交量變化 (%)", profiles[coin_a]["liquidity"]["volume_trend_pct"], profiles[coin_b]["liquidity"]["volume_trend_pct"]),
    ], profiles[coin_a]["liquidity"]["basis"])

    risk_rows = [("綜合暴險分數 (0-100)", profiles[coin_a]["risk_exposure"]["composite_score"], profiles[coin_b]["risk_exposure"]["composite_score"]),
                 ("可用組成項目數", profiles[coin_a]["risk_exposure"]["components_available"], profiles[coin_b]["risk_exposure"]["components_available"])]
    component_labels = {"funding_stress": "資金費率擁擠度", "positioning_crowding": "大戶持倉偏斜",
                        "rsi_extremity": "RSI 極端度", "timeframe_conflict": "時區訊號衝突", "realised_volatility": "已實現波動度"}
    for component, label in component_labels.items():
        risk_rows.append((label, profiles[coin_a]["risk_exposure"]["components"].get(component),
                          profiles[coin_b]["risk_exposure"]["components"].get(component)))
    risk_html = dimension("風險敞口", "Risk exposure", "risk_exposure", risk_rows, profiles[coin_a]["risk_exposure"]["basis"])

    attention_html = dimension("市場關注度", "Market attention", "attention", [
        ("社群互動總量（無上限）", profiles[coin_a]["attention"]["social_engagement_total"], profiles[coin_b]["attention"]["social_engagement_total"]),
        ("新聞則數（抓取上限 5）", profiles[coin_a]["attention"]["news_article_count"], profiles[coin_b]["attention"]["news_article_count"]),
        ("官方公告則數（抓取上限 5）", profiles[coin_a]["attention"]["official_announcement_count"], profiles[coin_b]["attention"]["official_announcement_count"]),
        ("社群貼文數（抓取上限 25）", profiles[coin_a]["attention"]["social_post_count"], profiles[coin_b]["attention"]["social_post_count"]),
    ], profiles[coin_a]["attention"]["basis"])

    def leg_card(coin: str) -> str:
        reasoning = payload["results"][coin]["reasoning"]
        confidence = max(0, min(100, round(float(reasoning.get("confidence", 0)) * 100)))
        return f"""<article class="insight-block"><span class="section-tag">{html.escape(coin)} 個別結論</span>
        <h3>信心度 {confidence}%</h3><p style="color:var(--ink-2);line-height:1.7">{html.escape(reasoning['market_judgment'])}</p>
        <div class="metric-sub" style="margin-top:10px">Evidence {len(payload['results'][coin]['evidence_ids'])} 筆
        ／完整報告輸出於 <code>outputs-day5/comparison/{html.escape(coin)}/report.md</code></div></article>"""

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(coin_a)} vs {html.escape(coin_b)} 比較研究</title><style>{BASE_CSS}</style></head><body>
    <main class="wrap"><div class="topbar"><div><span class="badge">比較分析完成</span>
    <h1 style="font-size:clamp(32px,4vw,48px)">{html.escape(coin_a)} vs {html.escape(coin_b)}</h1>
    <div class="muted">{html.escape(payload['question'])}</div></div><a class="back" href="/">＋ 新增研究</a></div>
    <section class="panel" style="margin-top:26px">
    <div class="report-head"><div><div class="report-kicker">Comparative Research Brief</div>
    <h2 style="margin:7px 0 0">三維度並列比較</h2></div>
    <div class="report-meta"><span class="meta-chip">{round(payload['duration_ms'] / 1000, 1)} 秒完成</span>
    <span class="meta-chip">共用單一時間預算</span></div></div>
    <article class="conclusion" style="margin-top:20px"><span class="section-tag">Comparative conclusion</span><h3>比較結論</h3>
    <p>{html.escape(comparison['summary'])}</p></article>
    {liquidity_html}{risk_html}{attention_html}
    <div class="report-grid">{leg_card(coin_a)}{leg_card(coin_b)}</div>
    <div class="disclaimer"><strong>比較限制</strong><span>{html.escape(comparison['caveat'])}</span></div>
    <details><summary>查看原始 comparison.md 內容</summary><pre class="raw">{html.escape(payload['markdown'])}</pre></details>
    </section></main></body></html>"""


_BACKTEST_CACHE: dict[str, dict] = {}


def _backtest_payload(coin: str) -> dict:
    """Run (or reuse) the walk-forward backtest for one coin.

    The replay takes a couple of seconds per coin, so results are memoised per process and keyed by
    the CSV's modification time -- editing the data file invalidates the entry instead of silently
    serving a stale run.
    """
    csv_path = Path("data") / f"{coin}.csv"
    if not csv_path.is_file():
        raise AgentInputError(f"找不到 {coin} 的歷史資料檔（data/{coin}.csv）")
    key = f"{coin}:{csv_path.stat().st_mtime_ns}"
    if key not in _BACKTEST_CACHE:
        rows = load_ohlcv(csv_path, coin, days=None)
        dates = [row["date"] for row in rows]
        _BACKTEST_CACHE[key] = {
            "coin": coin, "variants": compare_variants(rows, dates), "source": str(csv_path),
        }
    return _BACKTEST_CACHE[key]


def _backtest_page(payload: dict) -> str:
    coin = payload["coin"]
    variants = payload["variants"]
    primary = variants[0]
    window = primary["window"]

    def metric_card(label: str, value: str, sub: str = "") -> str:
        return (f'<div class="card"><div class="metric-label">{label}</div>'
                f'<div class="metric-value">{value}</div>'
                + (f'<div class="metric-sub">{sub}</div>' if sub else "") + "</div>")

    def signed(value, suffix: str = "%") -> str:
        if value is None:
            return "N/A"
        return f'<span class="{"up" if value > 0 else "down" if value < 0 else ""}">{value:+,.2f}{suffix}</span>'

    variant_rows = "".join(
        f"<tr><td><strong>{html.escape(variant['label'])}</strong></td>"
        f"<td>{variant['trade_count']}</td>"
        f"<td>{_metric(variant['win_rate_pct'], '%')}</td>"
        f"<td>{signed(variant['strategy_return_pct'])}</td>"
        f"<td>{signed(variant['buy_hold_return_pct'])}</td>"
        f"<td>{signed(variant['excess_return_pct'])}</td>"
        f"<td>{signed(variant['max_drawdown_pct'])}</td>"
        f"<td>{_metric(variant['exposure_pct'], '%')}</td></tr>"
        for variant in variants
    )
    exit_labels = {"take_profit": "策略停利", "reverse": "反向訊號平倉", "end_of_data": "資料結束強制平倉"}
    trade_rows = "".join(
        f"<tr><td>{html.escape(trade['entry_date'])} → {html.escape(trade['exit_date'])}</td>"
        f"<td>{'做多' if trade['side'] == 'long' else '做空'}</td>"
        f"<td>{trade['entry_price']:,.2f}</td><td>{trade['exit_price']:,.2f}</td>"
        f"<td>{trade['bars_held']} 天</td>"
        f"<td>{signed(trade['return_pct'])}</td>"
        f"<td>{html.escape(exit_labels.get(trade['exit_reason'], trade['exit_reason']))}</td></tr>"
        for trade in primary["trades"]
    ) or '<tr><td colspan="7" class="muted">此變體在測試區間內沒有完成任何一筆交易。</td></tr>'

    equity_chart = _interactive_chart(
        "chart-equity",
        [round(value, 4) for value in primary["equity_curve"]],
        primary["equity_dates"], baseline=1.0, unit=" 倍", height=210,
    )
    coin_links = "".join(
        f'<a class="tab{"" if other != coin else ""}" href="/backtest?coin={other}" '
        f'style="{"background:var(--brand);color:#fff" if other == coin else ""}">{other}</a>'
        for other in ("BTC", "ETH", "SOL", "BNB", "XRP")
    )
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(coin)} 策略回測</title><style>{BASE_CSS}</style></head><body>
    <main class="wrap"><div class="topbar"><div><span class="badge">Walk-forward 回測</span>
    <h1 style="font-size:clamp(32px,4vw,48px)">{html.escape(coin)} 策略歷史表現</h1>
    <div class="muted">Vegas 通道 + RSI(6) 進出場規則，於 {html.escape(window['date_start'])} 至 {html.escape(window['date_end'])} 的日線資料上逐根重放</div></div>
    <a class="back" href="/">← 回到研究首頁</a></div>
    <nav class="tabs" style="margin-top:26px">{coin_links}</nav>
    <section class="panel">
    <div class="report-head"><div><div class="report-kicker">Headline result</div>
    <h2 style="margin:7px 0 0">策略 vs 買進持有</h2></div>
    <div class="report-meta"><span class="meta-chip">測試 {window['test_bars']} 根日線</span>
    <span class="meta-chip">暖身 {window['warmup_bars']} 根</span>
    <span class="meta-chip">手續費 {primary['params']['fee_pct']}%／邊</span></div></div>
    <div class="metrics" style="margin-top:22px">
    {metric_card('策略報酬', signed(primary['strategy_return_pct']), '含手續費，複利計算')}
    {metric_card('買進持有', signed(primary['buy_hold_return_pct']), '同期間、同標的')}
    {metric_card('超額報酬', signed(primary['excess_return_pct']), '策略減去買進持有')}
    {metric_card('交易次數', str(primary['trade_count']), f"勝率 {_metric(primary['win_rate_pct'], '%')}")}
    </div>
    <article class="insight-block accent" style="margin-bottom:16px">
    <span class="section-tag">How to read this</span><h3>先看這裡：這份回測能證明什麼、不能證明什麼</h3>
    <ul class="insight-list">
    <li><strong>樣本數太少，不足以證明策略有效。</strong>整個測試區間只產生 {primary['trade_count']} 筆交易，
    這個數量無法區分「策略有邊」與「運氣」。任何勝率或報酬率都應視為觀察，不是統計結論。</li>
    <li><strong>時間框架與實際運行不同。</strong>線上 Agent 以 4H 趨勢 / 1H 執行判讀，但 EMA987 通道需要約
    1000 根 K 棒暖身，唯一夠長的歷史是日線 CSV，因此這裡測的是「同一套規則套在日線上」的表現。</li>
    <li><strong>已扣手續費（{primary['params']['fee_pct']}%／邊，來回 {primary['params']['fee_pct'] * 2}%），但未計滑價與資金費率。</strong>
    低頻策略受滑價影響較小，不過實際成交價仍會比收盤價差。</li>
    <li><strong>逐根重放，結構上不可能有前視偏誤。</strong>每一步只把「到當天為止」的 K 棒餵給策略函式，
    當日訊號才計入，隔日才可能平倉。</li>
    <li><strong>單一標的、單一參數、單一段歷史。</strong>切換上方幣種可以看規則在其他標的上是否一致，
    這是最低限度的穩健性檢查，仍不等於參數穩健。</li>
    </ul></article>
    <article class="insight-block" style="margin-bottom:16px"><span class="section-tag">Equity curve</span>
    <h3>資金曲線（起始 = 1.0 倍）</h3>
    <div class="muted" style="font-size:13px;margin-bottom:6px">持倉期間逐日按收盤價計算未實現損益，空手期間為水平線；虛線為起始本金。</div>
    {equity_chart}
    <div class="chart-caption"><span>{html.escape(window['date_start'])} → {html.escape(window['date_end'])}
    （{window['test_bars']} 個交易日）</span><span>移動滑鼠可顯示該日資金倍數</span></div></article>
    <article class="insight-block" style="margin-bottom:16px"><span class="section-tag">Variants</span>
    <h3>做多 only 與多空雙向的比較</h3>
    <div class="table-wrap"><table><thead><tr><th>變體</th><th>交易數</th><th>勝率</th><th>策略報酬</th>
    <th>買進持有</th><th>超額</th><th>最大回撤</th><th>持倉時間佔比</th></tr></thead><tbody>{variant_rows}</tbody></table></div>
    <div class="metric-sub" style="margin-top:10px">現貨只能做多；多空雙向需要合約，並且會額外承擔資金費率成本（此處未計入）。
    兩者數字相同時，代表測試期間沒有觸發任何做空進場訊號。</div></article>
    <article class="insight-block"><span class="section-tag">Trade log</span>
    <h3>{html.escape(primary['label'])} 的每一筆交易</h3>
    <div class="table-wrap"><table><thead><tr><th>期間</th><th>方向</th><th>進場價</th><th>出場價</th>
    <th>持有</th><th>報酬（含費）</th><th>出場原因</th></tr></thead><tbody>{trade_rows}</tbody></table></div>
    <div class="metrics" style="grid-template-columns:repeat(4,1fr);margin-top:16px">
    {metric_card('平均獲利', _metric(primary['avg_win_pct'], '%'), f"{primary['win_count']} 筆")}
    {metric_card('平均虧損', _metric(primary['avg_loss_pct'], '%'), f"{primary['loss_count']} 筆")}
    {metric_card('獲利因子', _metric(primary['profit_factor']), '總獲利 ÷ 總虧損')}
    {metric_card('平均持有', _metric(primary['avg_bars_held'], ' 天'), '進場到出場')}
    </div></article>
    <div class="disclaimer"><strong>研究聲明</strong><span>歷史回測不代表未來績效。本頁僅用於檢驗規則在既有資料上的表現，
    不構成投資建議、買賣指令或任何形式的財務承諾。</span></div>
    </section></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path)
        if route.path.rstrip("/") == "/backtest":
            coin = (parse_qs(route.query).get("coin", ["ETH"])[0] or "ETH").upper()
            try:
                self._send(_backtest_page(_backtest_payload(coin)))
            except (AgentInputError, ValueError) as error:
                self._send(
                    f"<html lang='zh-Hant'><meta charset='utf-8'><style>{BASE_CSS}</style>"
                    f"<main class='wrap'><section class='panel'><h1>無法產生回測</h1>"
                    f"<p>{html.escape(str(error))}</p><a href='/'>返回首頁</a></section></main></html>",
                    status=400,
                )
            return
        self._send(_home_page())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        # Never let a mis-encoded body kill the request thread and return an empty reply: browsers
        # post UTF-8 from this page, but a hand-rolled client on a non-UTF-8 console does not.
        values = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        coin = values.get("coin", ["ETH"])[0].upper()
        question = values.get("question", ["Market analysis"])[0]
        compare_with = values.get("compare_with", [""])[0].strip().upper()
        try:
            if compare_with:
                payload = run_comparison(
                    coin,
                    compare_with,
                    question,
                    Path("outputs-day5/comparison"),
                    live=True,
                    use_llm=llm_is_configured(),
                )
                self._send(_comparison_page(payload))
                return
            history_path = Path("data") / f"{coin}.csv"
            result = run(
                coin,
                question,
                Path("outputs-day5"),
                live=True,
                use_llm=llm_is_configured(),
                # The 5-year CSV is local and free: always use it for long-horizon context, while
                # the live market adapter keeps supplying the 14-day series the indicators run on.
                history_path=history_path if history_path.is_file() else None,
                fulltext=True,
            )
        except AgentInputError as error:
            self._send(
                f"<html lang='zh-Hant'><meta charset='utf-8'><style>{BASE_CSS}</style>"
                f"<main class='wrap'><section class='panel'><h1>輸入資料有誤</h1>"
                f"<p>{html.escape(str(error))}</p><a href='/'>返回重新輸入</a></section></main></html>",
                status=400,
            )
            return
        report = Path("outputs-day5/report.md").read_text(encoding="utf-8")
        evidence = json.loads(Path("outputs-day5/evidence.json").read_text(encoding="utf-8"))
        execution = json.loads(Path("outputs-day5/execution_log.json").read_text(encoding="utf-8"))
        self._send(_result_page(result, report, evidence, execution))

    def _send(self, body: str, status: int = 200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        return


def serve(port: int = 8000):
    loaded = load_dotenv()
    if loaded:
        print(f"[env] loaded from .env: {', '.join(loaded)}")
    info = llm_runtime_info()
    if llm_is_configured():
        print(f"[llm] {info['provider']} / {info['model']} - structured reasoning enabled")
    else:
        print(f"[llm] provider={info['provider']} - no API key, using deterministic fallback")
    print(f"[web] http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
