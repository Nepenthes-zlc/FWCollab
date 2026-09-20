"""Standalone HTML Canvas renderer for symbol observations."""

from __future__ import annotations

import html
import json
from pathlib import Path

from fwcollab.symbolic.map import SymbolMap
from fwcollab.symbolic.world import SymbolWorld


def render_symbol_html(symbol_map: SymbolMap, world: SymbolWorld | None = None) -> str:
    active_world = world or SymbolWorld(symbol_map)
    observation = active_world.observation()
    payload = json.dumps(observation, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(symbol_map.title)
    source = html.escape("\n".join(symbol_map.rows))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · FWCollab Symbol Map</title>
<style>
:root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
body {{ margin:0; background:#0f172a; color:#e2e8f0; }}
main {{ display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:18px; padding:18px; min-height:100vh; box-sizing:border-box; }}
.panel {{ background:#111c31; border:1px solid #334155; border-radius:12px; padding:14px; }}
canvas {{ display:block; width:100%; image-rendering:pixelated; background:#18243a; border-radius:8px; }}
h1 {{ margin:0 0 10px; font-size:20px; }}
.status {{ color:#93c5fd; margin-bottom:12px; }}
pre {{ overflow:auto; font:12px/1.45 ui-monospace,Consolas,monospace; white-space:pre; }}
.legend {{ display:grid; grid-template-columns:26px 1fr; gap:6px 10px; font-size:13px; }}
.key {{ text-align:center; font:700 15px ui-monospace,monospace; }}
@media(max-width:850px) {{ main {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<main>
  <section class="panel">
    <h1>{title}</h1>
    <div id="status" class="status"></div>
    <canvas id="map" aria-label="FWCollab symbol map"></canvas>
  </section>
  <aside class="panel">
    <h1>图例</h1>
    <div class="legend">
      <span class="key">F</span><span>火娃</span><span class="key">W</span><span>水娃</span>
      <span class="key">~</span><span>水池</span><span class="key">^</span><span>熔岩</span>
      <span class="key">1</span><span>持续压力板</span><span class="key">A/a</span><span>关闭/开启的门</span>
      <span class="key">L/l</span><span>未激活/已激活拨杆</span><span class="key">O</span><span>箱子</span>
      <span class="key">f/w</span><span>对应出口</span>
      <span class="key">P–R</span><span>成对传送门</span><span class="key">T/t</span><span>关闭/开启的切换开关</span>
      <span class="key">I/~</span><span>冻结/液态热相地块</span><span class="key">H/K</span><span>加热器/冻结器</span>
      <span class="key">+/S</span><span>光源/光感应器</span><span class="key">/ \\</span><span>镜面</span>
      <span class="key">=/-</span><span>收起/展开的移动桥</span><span class="key">J</span><span>单向门</span>
      <span class="key">o</span><span>滚动机关球</span>
    </div>
    <h1 style="margin-top:18px">原始符号图</h1>
    <pre>{source}</pre>
  </aside>
</main>
<script id="fwcollab-state" type="application/json">{payload}</script>
<script>
(() => {{
  const canvas = document.getElementById('map');
  const context = canvas.getContext('2d');
  const status = document.getElementById('status');
  let state = JSON.parse(document.getElementById('fwcollab-state').textContent);
  const colors = {{'#':'#475569','.':'#172033','~':'#287dcc','^':'#d94b2b','x':'#54a853','F':'#ff6048','W':'#48b5ff','&':'#b77cff','O':'#b88752','o':'#94a3b8','f':'#44232a','w':'#173d59','P':'#a855f7','Q':'#8b5cf6','R':'#7c3aed','T':'#0f766e','t':'#14b8a6','I':'#bae6fd','H':'#f97316','K':'#06b6d4','+':'#facc15','S':'#713f12','s':'#fde047',':':'#fef08a','=':'#1e293b','-':'#22c55e','J':'#f59e0b','/':'#cbd5e1'}};
  function drawCell(cell, x, y, size) {{
    context.fillStyle = colors[cell] || (/[A-E]/.test(cell) ? '#eab308' : (/[a-e]/.test(cell) ? '#64748b' : '#172033'));
    context.fillRect(x, y, size, size);
    context.strokeStyle = '#26364f'; context.strokeRect(x, y, size, size);
    if (!'#.~^x:-'.includes(cell)) {{
      context.fillStyle = cell === 'F' || cell === 'W' ? '#0f172a' : '#f8fafc';
      context.font = `bold ${{Math.max(12, size * .48)}}px ui-monospace, monospace`;
      context.textAlign = 'center'; context.textBaseline = 'middle';
      context.fillText(cell, x + size/2, y + size/2);
    }}
  }}
  function render() {{
    const rows = state.map_rows, cols = rows[0].length;
    const size = Math.max(12, Math.min(34, Math.floor((window.innerWidth - 390) / cols)));
    canvas.width = cols * size; canvas.height = rows.length * size;
    rows.forEach((row, r) => [...row].forEach((cell, c) => drawCell(cell, c*size, r*size, size)));
    status.textContent = `map=${{state.map_id}} · round=${{state.round}} · micro=${{state.micro_steps}} · ${{state.status}}`;
  }}
  window.renderFWCollabState = nextState => {{ state = nextState; render(); }};
  window.addEventListener('resize', render); render();
}})();
</script>
</body>
</html>
"""


def save_symbol_html(path: str | Path, symbol_map: SymbolMap, world: SymbolWorld | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_symbol_html(symbol_map, world), encoding="utf-8")


def render_symbol_gallery(symbol_maps: list[SymbolMap]) -> str:
    """Render multiple initial observations into one self-contained overview."""

    if not symbol_maps:
        raise ValueError("gallery requires at least one symbol map")
    entries = [
        {
            "id": symbol_map.map_id,
            "title": symbol_map.title,
            "observation": SymbolWorld(symbol_map).observation(),
        }
        for symbol_map in symbol_maps
    ]
    payload = json.dumps(entries, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FWCollab · 核心地图总览</title>
<style>
:root {{ color-scheme:dark; font-family:Inter,system-ui,sans-serif; }}
body {{ margin:0; padding:22px; background:#0b1220; color:#e2e8f0; }}
header {{ margin:0 auto 18px; max-width:1500px; }}
h1 {{ margin:0 0 6px; font-size:26px; }}
p {{ margin:0; color:#94a3b8; }}
#gallery {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(390px,1fr)); gap:16px; max-width:1500px; margin:auto; }}
.card {{ background:#111c31; border:1px solid #334155; border-radius:12px; padding:13px; overflow:auto; }}
.title {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:10px; font-weight:700; }}
.id {{ color:#7dd3fc; font-family:ui-monospace,monospace; }}
canvas {{ display:block; image-rendering:pixelated; background:#18243a; border-radius:7px; max-width:none; }}
</style>
</head>
<body>
<header><h1>FWCollab 核心地图集</h1><p>{len(entries)} 张地图的初始公开状态；状态更新仍由 Python 权威规则模块负责。</p></header>
<main id="gallery"></main>
<script id="fwcollab-gallery" type="application/json">{payload}</script>
<script>
(() => {{
  const entries = JSON.parse(document.getElementById('fwcollab-gallery').textContent);
  const gallery = document.getElementById('gallery');
  const colors = {{'#':'#475569','.':'#172033','~':'#287dcc','^':'#d94b2b','x':'#54a853','F':'#ff6048','W':'#48b5ff','&':'#b77cff','O':'#b88752','o':'#94a3b8','f':'#44232a','w':'#173d59','P':'#a855f7','Q':'#8b5cf6','R':'#7c3aed','T':'#0f766e','t':'#14b8a6','I':'#bae6fd','H':'#f97316','K':'#06b6d4','+':'#facc15','S':'#713f12','s':'#fde047',':':'#fef08a','=':'#1e293b','-':'#22c55e','J':'#f59e0b','/':'#cbd5e1'}};
  for (const entry of entries) {{
    const card = document.createElement('section'); card.className = 'card';
    const heading = document.createElement('div'); heading.className = 'title';
    heading.innerHTML = `<span>${{entry.title}}</span><span class="id">${{entry.id}}</span>`;
    const canvas = document.createElement('canvas'); const rows = entry.observation.map_rows;
    const size = Math.max(16, Math.min(26, Math.floor(570 / rows[0].length)));
    canvas.width = rows[0].length * size; canvas.height = rows.length * size;
    const context = canvas.getContext('2d');
    rows.forEach((row, r) => [...row].forEach((cell, c) => {{
      context.fillStyle = colors[cell] || (/[A-E]/.test(cell) ? '#eab308' : '#172033');
      context.fillRect(c*size, r*size, size, size); context.strokeStyle='#26364f'; context.strokeRect(c*size,r*size,size,size);
      if (!'#.~^x:-'.includes(cell)) {{
        context.fillStyle = cell === 'F' || cell === 'W' ? '#0f172a' : '#f8fafc';
        context.font = `bold ${{Math.max(11,size*.47)}}px ui-monospace,monospace`;
        context.textAlign='center'; context.textBaseline='middle'; context.fillText(cell,c*size+size/2,r*size+size/2);
      }}
    }}));
    card.append(heading, canvas); gallery.append(card);
  }}
}})();
</script>
</body>
</html>
"""


def save_symbol_gallery(path: str | Path, symbol_maps: list[SymbolMap]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_symbol_gallery(symbol_maps), encoding="utf-8")


def render_trace_html(trace: dict[str, object]) -> str:
    """Render a trace as a self-contained, state-only interactive replay."""

    if trace.get("format") != "fwcollab.symbol_trace.v1":
        raise ValueError("unsupported trace format")
    rounds = trace.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("trace has no rounds")
    frames = [{"round": 0, "observation": rounds[0]["observation"], "agents": None, "status": "initial"}]
    frames.extend(
        {
            "round": item["round"],
            "observation": item["result"]["observation"],
            "agents": item["agents"],
            "status": item["result"]["status"],
        }
        for item in rounds
    )
    payload = json.dumps(
        {
            "frames": frames,
            "metrics": trace.get("metrics", {}),
            "diagnosis": trace.get("diagnosis", {}),
            "evaluation": trace.get("private_evaluation", {}),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    title = html.escape(str(trace.get("map_id", "trace")))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 双智能体回放</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif}}body{{margin:0;background:#0b1220;color:#e2e8f0}}
main{{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:16px;padding:18px}}.panel{{background:#111c31;border:1px solid #334155;border-radius:12px;padding:14px}}
canvas{{display:block;max-width:100%;image-rendering:pixelated;background:#18243a;border-radius:8px}}button{{margin:8px 8px 0 0;padding:7px 12px}}
pre{{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;max-height:55vh;overflow:auto}}#diagnosis{{padding:9px;border-radius:8px;background:#17233a;color:#fca5a5;font:12px/1.5 ui-monospace,monospace}}@media(max-width:850px){{main{{grid-template-columns:1fr}}}}
</style></head><body><main><section class="panel"><h2>{title} 双智能体单步回放</h2><div id="status"></div><canvas id="map"></canvas>
<button id="prev">上一轮</button><button id="play">播放</button><button id="next">下一轮</button><input id="slider" type="range" min="0" value="0" style="width:100%"></section>
<aside class="panel"><h3>最终诊断</h3><div id="diagnosis"></div><h3>本轮模型记录</h3><pre id="detail"></pre></aside></main>
<script id="trace-data" type="application/json">{payload}</script><script>(()=>{{
const data=JSON.parse(document.getElementById('trace-data').textContent),frames=data.frames,canvas=document.getElementById('map'),ctx=canvas.getContext('2d'),slider=document.getElementById('slider');
const colors={{'#':'#475569','.':'#172033','~':'#287dcc','^':'#d94b2b','x':'#54a853','F':'#ff6048','W':'#48b5ff','&':'#b77cff','O':'#b88752','o':'#94a3b8','f':'#44232a','w':'#173d59','P':'#a855f7','Q':'#8b5cf6','R':'#7c3aed','T':'#0f766e','t':'#14b8a6','I':'#bae6fd','H':'#f97316','K':'#06b6d4','+':'#facc15','S':'#713f12','s':'#fde047',':':'#fef08a','=':'#1e293b','-':'#22c55e','J':'#f59e0b','/':'#cbd5e1'}};
let index=0,timer=null;slider.max=frames.length-1;document.getElementById('diagnosis').textContent=JSON.stringify(data.diagnosis&&data.diagnosis.primary?data.diagnosis:{{primary:'none (success or legacy trace)'}},null,2);function draw(){{const frame=frames[index],rows=frame.observation.map_rows,size=Math.max(15,Math.min(34,Math.floor(760/rows[0].length)));canvas.width=rows[0].length*size;canvas.height=rows.length*size;
rows.forEach((row,r)=>[...row].forEach((cell,c)=>{{ctx.fillStyle=colors[cell]||(/[A-E]/.test(cell)?'#eab308':(/[a-e]/.test(cell)?'#64748b':'#172033'));ctx.fillRect(c*size,r*size,size,size);ctx.strokeStyle='#26364f';ctx.strokeRect(c*size,r*size,size,size);if(!'#.~^x:-'.includes(cell)){{ctx.fillStyle=(cell==='F'||cell==='W')?'#0f172a':'#f8fafc';ctx.font=`bold ${{Math.max(11,size*.47)}}px ui-monospace`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(cell,c*size+size/2,r*size+size/2)}}}}));
const progress=data.evaluation&&data.evaluation.progress?data.evaluation.progress[index]:null;const progressText=progress?` · DAG=${{progress.completed}}/${{progress.total}}`:'';document.getElementById('status').textContent=`frame ${{index}}/${{frames.length-1}} · round=${{frame.round}} · ${{frame.status}}${{progressText}}`;document.getElementById('detail').textContent=JSON.stringify({{agents:frame.agents||{{note:'初始状态',metrics:data.metrics}},dag_progress:progress}},null,2);slider.value=index}}
function step(delta){{index=Math.max(0,Math.min(frames.length-1,index+delta));draw()}}document.getElementById('prev').onclick=()=>step(-1);document.getElementById('next').onclick=()=>step(1);slider.oninput=()=>{{index=Number(slider.value);draw()}};
document.getElementById('play').onclick=e=>{{if(timer){{clearInterval(timer);timer=null;e.target.textContent='播放'}}else{{timer=setInterval(()=>{{if(index>=frames.length-1){{clearInterval(timer);timer=null;e.target.textContent='播放'}}else step(1)}},550);e.target.textContent='暂停'}}}};draw();
}})();</script></body></html>"""
def save_trace_html(path: str | Path, trace: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_trace_html(trace), encoding="utf-8")
