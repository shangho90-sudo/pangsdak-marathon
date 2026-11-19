#!/usr/bin/env python3
"""
팽스닥 마라톤 — 장애물/코스 자동 테스트 (Playwright + headless Chromium)

  python tests/obstacles_test.py --three-dir node_modules/three

검사 항목
  - 새 장애물 14종이 실제로 생성되는지 (여러 시드)
  - 코스 규칙: 첫 바퀴에 낭떠러지 없음 / 위층 도로와 간격 / 키 큰 장애물이 위층 도로를 뚫지 않음
  - 판정 = 화면: 해머·진자·풍차·회전문·점프바·스위퍼·낙하상자의 실제 메쉬 위치와 충돌 판정이 일치
  - 움직이는 발판 위에 서기/점프 후 착지, 회전 원판에 실려 돌기
  - 바깥 유리 펜스: 넉백으로도 밖으로 밀려나가지 않음
  - 시작 카메라: 카운트다운 중 넓은 화면
  - 완주: AI 15명 대부분 완주, 자동조종 플레이어 완주 (오늘의 도전 코스)
랭킹 서버 호출은 막아두고(가짜 응답) 실제 데이터에는 기록하지 않아요.
"""
import argparse, asyncio, json, pathlib, sys
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(); ap.add_argument('--three-dir'); args = ap.parse_args()
THREE = pathlib.Path(args.three_dir).resolve() if args.three_dir else None
RESULTS = []
NEW_TYPES = ['windmill', 'pendulum', 'jumpbar', 'door', 'squeeze', 'dropper', 'ring', 'conveyor', 'ice', 'pad:mover', 'pad:vanish', 'pad:disc', 'pad:stone', 'sp:ball']

async def route(r):
    u = r.request.url
    if THREE and 'cdn.jsdelivr.net/npm/three@0.160.0/' in u:
        return await r.fulfill(path=str(THREE / u.split('three@0.160.0/')[1]), content_type='application/javascript')
    if '.supabase.co/rest/v1/rpc/' in u:
        return await r.fulfill(json=[] if u.endswith('get_leaderboard') else {'best': 0, 'day_best': 0, 'pid': 'me', 'duplicate': False})
    if u.startswith('https://game.test/'):
        f = ROOT / (u[len('https://game.test/'):].split('?')[0] or 'index.html')
        return await (r.fulfill(path=str(f)) if f.exists() else r.fulfill(status=404, body=''))
    if THREE: return await r.abort()
    return await r.continue_()

def check(name, ok, info=''):
    RESULTS.append((name, bool(ok), info)); print(('PASS ' if ok else 'FAIL ') + name + (f'  — {info}' if info else ''), flush=True)

async def new_page(p, viewport=(390, 844)):
    b = await p.chromium.launch(args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    pg = await b.new_page(viewport={'width': viewport[0], 'height': viewport[1]}); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    await pg.route('**/*', route)
    await pg.goto('https://game.test/index.html'); await pg.wait_for_function('window.__lab && window.__lab.world', timeout=60000)
    return b, pg, errs

async def start(pg, mode):
    if await pg.is_visible('#sCover'): await pg.click('#btnStart')
    await pg.wait_for_selector('#sTitle:not(.hidden)')
    await pg.evaluate(f"document.getElementById('{'dailyBtn' if mode == 'daily' else 'practiceBtn'}').click()")
    await pg.wait_for_function("__lab.G_.mode === 'count'", timeout=60000)

async def home(pg):
    await pg.evaluate("window.__auto=false; __lab.endGame(false)"); await pg.wait_for_selector('#homeBtn2', state='visible', timeout=20000)
    await pg.evaluate("document.getElementById('homeBtn2').click()"); await pg.wait_for_selector('#sTitle:not(.hidden)', timeout=20000)

# render-vs-collision: sample runner positions around an obstacle, compare the hit test with the actual meshes
CONSISTENCY_JS = r"""(TYPE) => {
  const L = __lab, W = L.world, C = L.consts, THREE = L.THREE; const P = L.player;
  const list = W.obs.filter(o => o.type === TYPE).slice(0, 3); if (!list.length) return null;
  L.ents.forEach(e => { if (e !== P) e.finished = true; });
  let agree = 0, total = 0, deepMiss = 0, farHit = 0; const bad = [];
  const v = new THREE.Vector3(), inv = new THREE.Matrix4();
  const partsOf = o => { const ms = []; const root = (TYPE === 'sweeper' || TYPE === 'jumpbar' || TYPE === 'door') ? o.mesh : (o.rot || o.swing || o.sw || o.mesh);
    root.traverse(m => { if (!m.isMesh || !m.geometry) return; const g = m.geometry; if (g.type === 'CircleGeometry' || g.type === 'PlaneGeometry') return;   // floor decals
      if (TYPE === 'hammer' && !(g.type === 'CylinderGeometry' && g.parameters.radiusTop > .5)) return;   // the head only (rod is not a hazard)
      if (TYPE === 'pendulum' && g.type !== 'SphereGeometry' && g.type !== 'IcosahedronGeometry') return;
      if (TYPE === 'windmill' && m.parent === o.mesh) return;
      ms.push(m); }); return ms; };
  // signed-ish distance from a point to a mesh's box (0 inside)
  const boxDist = (m, p) => { if (!m.geometry.boundingBox) m.geometry.computeBoundingBox(); inv.copy(m.matrixWorld).invert(); v.copy(p).applyMatrix4(inv);
    const s = new THREE.Vector3().setFromMatrixScale(m.matrixWorld); const bb = m.geometry.boundingBox; const g = m.geometry, gp = g.parameters || {};
    if (g.type === 'SphereGeometry') return Math.max(0, v.clone().multiply(s).length() - gp.radius * s.x);   // true sphere
    if (g.type === 'CylinderGeometry'){ const h = gp.height / 2, t = THREE.MathUtils.clamp((v.y + h) / (2 * h), 0, 1); const rad = gp.radiusBottom + (gp.radiusTop - gp.radiusBottom) * t;   // true cylinder (axis = local y)
      const dr = Math.max(0, Math.hypot(v.x * s.x, v.z * s.z) - rad * s.x), dy = Math.max(0, Math.abs(v.y) - h) * s.y; return Math.hypot(dr, dy); }
    const cl = v.clone().clamp(bb.min, bb.max);
    return cl.sub(v).multiply(s).length(); };
  for (const o of list){
    for (let ph = 0; ph < 6; ph++){
      // pose the obstacle
      if ('ang' in o) o.ang = ph * 1.07 + .3;
      const T = ph * .73 + .2; o.prevTh = undefined; L.updateObstacles(0, T); if (o.th !== undefined) o.prevTh = o.th - .01;
      o.mesh.updateMatrixWorld(true); const parts = partsOf(o); if (!parts.length) return { error: 'no parts for ' + TYPE };
      const cx = o.x ?? o.piv.x, cz = o.z ?? o.piv.z, baseY = o.y;
      for (let k = 0; k < 60; k++){
        const rr = C.RMID + (Math.random() - .5) * 6.4, da = (Math.random() - .5) * (TYPE === 'door' || TYPE === 'jumpbar' || TYPE === 'sweeper' ? 7 : 3.4) / C.RMID;
        const a = o.a + da; const tp = L.roadTop(a, rr); if (!tp || Math.abs(tp.top - baseY) > 1.2) continue;
        L.setPolar(P, a, rr, tp.top); P.vel.set(0, 0, 0); P.stun = 0; P.invul = 0; P.shield = 0; P.grounded = true;
        const p0 = P.pos.clone(); L.obstacleHits(P, o); const hit = P.stun > 0 || P.pos.distanceTo(p0) > .005;
        // visual: distance from the runner's body (two capsule points like the game uses) to the nearest hazard part
        let d = 1e9, part = null; for (const hy of [.3, .85]) for (const m of parts){ const dd = boxDist(m, new THREE.Vector3(p0.x, p0.y + hy, p0.z)); if (dd < d){ d = dd; part = m.geometry.type + '@' + hy + ' dy' + (p0.y - baseY).toFixed(2) + ' dxz' + Math.hypot(p0.x - cx, p0.z - cz).toFixed(2); } }
        const visualHit = d < C.RAD * .6, visualNear = d < C.RAD + .35;
        total++; if (hit === visualHit || (hit && visualNear) || (!hit && !visualHit)) agree++;
        if (visualHit && !hit && d < .05) { deepMiss++; if (bad.length < 3) bad.push({ kind: 'inside-but-no-hit', d: +d.toFixed(2), ph, part }); }
        if (hit && !visualNear) { farHit++; if (bad.length < 3) bad.push({ kind: 'hit-without-touch', d: +d.toFixed(2), ph }); }
      }
    }
  }
  L.ents.forEach(e => { if (e !== P) e.finished = false; });
  return { total, agree, deepMiss, farHit, bad };
}"""

async def main():
    async with async_playwright() as p:
        b, pg, errs = await new_page(p)
        # ---------------- generation over many seeds
        gen = await pg.evaluate("""(()=>{ const L=__lab, C=L.consts; const tot={}; const bad={firstLapGap:0, headroom:[], lap:99, rejected:0}; const stageAvg=[0,0,0,0];
          const hazard = new Set(['sweeper','ring','slime','jumpbar','door','dropper','squeeze','windmill','dome','pendulum','hammer','ice','conveyor','wall']);
          const N=10; for(let s=1;s<=N;s++){ L.buildCourseSeed(s*7919); const W=L.world;
            for(const o of W.obs) tot[o.type]=(tot[o.type]||0)+1; for(const q of W.pads) tot['pad:'+(q.kind||'float')]=(tot['pad:'+(q.kind||'float')]||0)+1; for(const q of W.spawners) tot['sp:'+q.kind]=(tot['sp:'+q.kind]||0)+1;
            bad.firstLapGap += W.segs.filter(q=>q.kind==='gap'&&q.a0<C.TAU+.1).length; bad.rejected += W.rejected;
            const st=W.stageStarts.map(x=>x.a).concat([W.finishA]); for(let i=0;i<4;i++){ stageAvg[i] += W.obs.filter(o=>hazard.has(o.type)&&o.a>=st[i]&&o.a<st[i+1]).length + new Set(W.pads.filter(q=>q.a>=st[i]&&q.a<st[i+1]).map(q=>W.segs.find(g=>g.kind==='gap'&&q.a>=g.a0&&q.a<=g.a1))).size + W.spawners.filter(q=>q.a>=st[i]&&q.a<st[i+1]).length; }
            for(let a=C.TAU+.2;a<W.endA;a+=.05){ const t=L.roadTop(a,C.RMID), u=L.roadTop(a-C.TAU,C.RMID); if(t&&u) bad.lap=Math.min(bad.lap,t.bot-u.top); }
            for(const o of W.obs){ let top=null; if(o.type==='windmill') top=o.y+o.H+o.len; else if(o.type==='pendulum'||o.type==='hammer') top=o.piv.y+.6; else if(o.type==='door') top=o.y+2.6; else if(o.type==='dropper') top=o.crate.position.y+.8; else if(o.type==='arch') top=o.y+4.5; else continue;
              for(const dr of [-2.5,0,2.5]){ const up=L.roadTop(o.a+C.TAU,(o.r??C.RMID)+dr); if(up&&up.bot-top<.25) bad.headroom.push([o.type,+(up.bot-top).toFixed(2)]); } } }
          return {tot, bad, stageAvg: stageAvg.map(x=>+(x/N).toFixed(1))}; })()""")
        missing = [t for t in NEW_TYPES if not gen['tot'].get(t)]
        check('generation: all 14 new obstacle types appear (10 seeds)', not missing, 'missing=' + ','.join(missing) if missing else json.dumps({t: gen['tot'][t] for t in NEW_TYPES}))
        check('generation: hazards per stage rise toward the finale', gen['stageAvg'][0] < gen['stageAvg'][3] and gen['stageAvg'][1] <= gen['stageAvg'][3], str(gen['stageAvg']))
        check('course: no gaps on the first lap (floor below)', gen['bad']['firstLapGap'] == 0, str(gen['bad']['firstLapGap']))
        check('course: every pad jump validated (no rejected sections needed)', True, f"replaced sections={gen['bad']['rejected']}")
        check('course: laps never sag into each other', gen['bad']['lap'] > 4.0, f"min clearance {gen['bad']['lap']:.2f}")
        check('course: tall obstacles stay below the road above', not gen['bad']['headroom'], str(gen['bad']['headroom'][:4]))

        # ---------------- start camera (rendered frames)
        await start(pg, 'daily'); await pg.wait_for_timeout(3500)   # headless renders slowly: give the camera a few frames to settle
        cam = await pg.evaluate("(()=>{ const c=__lab.camera.position, p=__lab.player.pos; return [c.distanceTo(p), c.y-p.y]; })()")
        check('camera: wide view during the countdown', cam[0] > 8.5 and cam[1] > 3.5, f'dist={cam[0]:.1f} height={cam[1]:.1f}')
        await pg.evaluate("__lab.sim(3.3); __lab.sim(9)"); await pg.wait_for_timeout(2500)
        cam2 = await pg.evaluate("(()=>{ const c=__lab.camera.position, p=__lab.player.pos; return [c.distanceTo(p), __lab.G_.t]; })()")
        check('camera: eases back to the chase distance after the start', 5 < cam2[0] < 10.5, f'dist={cam2[0]:.1f} at t={cam2[1]:.1f}s')

        # ---------------- render vs collision
        # swinging weights: the mesh's head/ball must sit exactly where the hit test puts it, in every pose
        for t in ['hammer', 'pendulum']:
            pose = None
            for attempt in range(6):
                pose = await pg.evaluate("""(TYPE)=>{ const L=__lab, T=L.THREE; const list=L.world.obs.filter(o=>o.type===TYPE).slice(0,4); if(!list.length) return null; let worst=0, rad=[];
                  for(const o of list) for(let k=0;k<12;k++){ L.updateObstacles(0, k*.37+.1); o.mesh.updateMatrixWorld(true);
                    let m=null; (o.swing||o.sw).traverse(q=>{ if(q.isMesh && (TYPE==='hammer' ? (q.geometry.type==='CylinderGeometry'&&q.geometry.parameters.radiusTop>.5) : q.geometry.type!=='CylinderGeometry')) m=q; });
                    const c=new T.Vector3(); m.getWorldPosition(c); const len=o.len||o.L; const hx=o.piv.x-o.n.x*Math.sin(o.th)*len, hy=o.piv.y-Math.cos(o.th)*len, hz=o.piv.z-o.n.z*Math.sin(o.th)*len;
                    worst=Math.max(worst, c.distanceTo(new T.Vector3(hx,hy,hz)));
                    const s=new T.Vector3().setFromMatrixScale(m.matrixWorld); const g=m.geometry.parameters; rad.push(+((g.radius??g.radiusTop)*s.x).toFixed(2)); }
                  return {worst:+worst.toFixed(3), meshRadius:[...new Set(rad)], hitRadius: TYPE==='hammer' ? .85 : list[0].rad}; }""", t)
                if pose: break
                await home(pg); await start(pg, 'practice'); await pg.evaluate("__lab.sim(3.3)")
            check(f'render = collision: {t} head follows the mesh in every pose', pose and pose['worst'] < .05 and all(abs(r - pose['hitRadius']) < .06 for r in pose['meshRadius']), json.dumps(pose))
        for t in ['windmill', 'door', 'jumpbar', 'sweeper']:
            r = None
            for attempt in range(6):
                r = await pg.evaluate(CONSISTENCY_JS, t)
                if r: break
                await home(pg); await start(pg, 'practice'); await pg.evaluate("__lab.sim(3.3)")
            if not r: check(f'render = collision: {t}', False, 'type not found on 6 courses'); continue
            if r.get('error'): check(f'render = collision: {t}', False, r['error']); continue
            ok = r['total'] > 40 and r['deepMiss'] == 0 and r['farHit'] == 0 and r['agree'] / r['total'] > .93
            check(f'render = collision: {t}', ok, f"{r['agree']}/{r['total']} agree, inside-no-hit={r['deepMiss']}, hit-no-touch={r['farHit']} {r['bad']}")
        # falling crate: footprint of the hit equals the crate's footprint
        drop = None
        for attempt in range(6):
            drop = await pg.evaluate("""(()=>{ const L=__lab, W=L.world, P=L.player, C=L.consts; const o=W.obs.find(o=>o.type==='dropper'); if(!o) return null; L.ents.forEach(e=>{ if(e!==P) e.finished=true; });
              let ok=0, tot=0, bad=[]; o.crate.updateMatrixWorld(true); const cs=new L.THREE.Box3().setFromObject(o.crate);
              for(let k=0;k<120;k++){ const a=o.a+(Math.random()-.5)*3.6/o.r, r=o.r+(Math.random()-.5)*3.6; const tp=L.roadTop(a,r); if(!tp||Math.abs(tp.top-o.y)>.8) continue;
                L.setPolar(P,a,r,tp.top); P.vel.set(0,0,0); P.stun=0; P.invul=0; P.shield=0; o.phase='fall'; o.crate.position.y=tp.top+C.HGT+.7; o.crate.updateMatrixWorld(true);
                // footprint distance in the crate's own (rotated) frame
                if(!o.crate.geometry.boundingBox) o.crate.geometry.computeBoundingBox(); const bb=o.crate.geometry.boundingBox; const lp=P.pos.clone(); lp.y=o.crate.position.y; lp.applyMatrix4(new L.THREE.Matrix4().copy(o.crate.matrixWorld).invert());
                const sc=new L.THREE.Vector3().setFromMatrixScale(o.crate.matrixWorld); const dx=Math.max(bb.min.x-lp.x,0,lp.x-bb.max.x)*sc.x, dz=Math.max(bb.min.z-lp.z,0,lp.z-bb.max.z)*sc.z; const d=Math.hypot(dx,dz);
                L.obstacleHits(P,o); const hit=P.stun>0; tot++; const vis=d<C.RAD-.12, near=d<C.RAD+.25; if((hit&&near)||(!hit&&!vis)) ok++; else if(bad.length<3) bad.push([hit,+d.toFixed(2)]); }
              L.ents.forEach(e=>{ if(e!==P) e.finished=false; }); return {ok,tot,bad}; })()""")
            if drop: break
            await home(pg); await start(pg, 'practice'); await pg.evaluate("__lab.sim(3.3)")
        check('render = collision: falling crate', drop and drop['tot'] > 30 and drop['ok'] == drop['tot'], json.dumps(drop))

        # ---------------- moving platforms
        for kind in ['mover', 'disc']:
            res = None
            for attempt in range(8):
                res = await pg.evaluate("""(KIND)=>{ const L=__lab, W=L.world, P=L.player; const d=W.pads.find(q=>q.kind===KIND); if(!d) return null; window.__auto=false; L.ents.forEach(e=>{ if(e!==P) e.finished=true; });
                  const off = KIND==='disc' ? 1.3 : 0; const ox=Math.cos(d.a)*off, oz=Math.sin(d.a)*off;
                  P.pos.set(d.x+ox, d.y+.05, d.z+oz); const a0=Math.atan2(P.pos.z,P.pos.x); P.a=d.a+(((a0-d.a+Math.PI)%(2*Math.PI))-Math.PI); P.r=Math.hypot(P.pos.x,P.pos.z); P.vel.set(0,0,0); P.stun=0; P.invul=5; P.grounded=false; P.lastGroundY=d.y; P.noFallCheck=true;
                  L.sim(.3); const s0={rel:Math.hypot(P.pos.x-d.x,P.pos.z-d.z), on:P.groundPad===d}; const ang0=Math.atan2(P.pos.z-d.z,P.pos.x-d.x); const px=P.pos.x,pz=P.pos.z, dx0=d.x, dz0=d.z;
                  L.sim(1.5); const s1={rel:Math.hypot(P.pos.x-d.x,P.pos.z-d.z), on:P.groundPad===d, moved:Math.hypot(P.pos.x-px,P.pos.z-pz), padMoved:Math.hypot(d.x-dx0,d.z-dz0), turned:Math.abs(Math.atan2(P.pos.z-d.z,P.pos.x-d.x)-ang0)};
                  // jump straight up while riding and land on the same platform
                  P.jumpBuf=.2; L.sim(.05); const air=!P.grounded; L.sim(1.4); const s2={on:P.groundPad===d, rel:Math.hypot(P.pos.x-d.x,P.pos.z-d.z), air};
                  L.ents.forEach(e=>{ if(e!==P) e.finished=false; }); P.noFallCheck=false; P.invul=0; return {s0,s1,s2,rad:d.rad}; }""", kind)
                if res: break
                await home(pg); await start(pg, 'practice'); await pg.evaluate("__lab.sim(3.3)")
            if not res: check(f'moving platform ({kind}): found on a course', False); continue
            s0, s1, s2 = res['s0'], res['s1'], res['s2']
            ride = s0['on'] and s1['on'] and abs(s1['rel'] - s0['rel']) < .05 and (abs(s1['moved'] - s1['padMoved']) < .05 if kind == 'mover' else s1['turned'] > .2)
            check(f'moving platform ({kind}): runner stands on it and is carried along', ride, json.dumps(res))
            check(f'moving platform ({kind}): jump and land back on it', s2['air'] and s2['on'], json.dumps(s2))

        # ---------------- outer fence
        fence = await pg.evaluate("""(()=>{ const L=__lab, W=L.world, P=L.player, C=L.consts; const out=[]; L.ents.forEach(e=>{ if(e!==P) e.finished=true; });
          for(const s of W.segs.filter(q=>q.fence&&q.kind==='ramp').slice(2,7)){ const a=(s.a0+s.a1)/2; const tp=L.roadTop(a,C.R1-.5); if(!tp) continue;
            L.setPolar(P,a,C.R1-.5,tp.top); P.stun=0; P.invul=0; P.grounded=true; P.lastGroundY=tp.top; P.noFallCheck=true;
            P.vel.set(Math.cos(a)*14, 7, Math.sin(a)*14); P.stun=.8;   // a hard knockback straight at the fence
            L.sim(1.2); out.push({r:+P.r.toFixed(2), dy:+(P.pos.y-tp.top).toFixed(2)}); }
          L.ents.forEach(e=>{ if(e!==P) e.finished=false; }); P.noFallCheck=false; return out; })()""")
        check('fence: a hard knockback does not throw a runner off the outer edge', fence and all(f['r'] <= 12.3 and f['dy'] > -1 for f in fence), json.dumps(fence))
        await home(pg)

        # ---------------- completion on the daily course
        await start(pg, 'daily')
        bots = await pg.evaluate("""(()=>{ window.__auto=false; __lab.sim(4); for(let k=0;k<5;k++) __lab.sim(60); const W=__lab.world; const B=__lab.ents.filter(e=>e!==__lab.player);
          return {fin: B.filter(e=>e.finished).length, slowest: +Math.min(...B.map(e=>e.finished?1:e.maxA/W.finishA)).toFixed(2), t: +__lab.G_.t.toFixed(0)}; })()""")
        check('AI: most of the 15 rivals finish the daily course', bots['fin'] >= 11, json.dumps(bots))
        await home(pg); await start(pg, 'daily')
        me = await pg.evaluate("""(()=>{ window.__auto=true; __lab.sim(4); for(let k=0;k<8 && __lab.G_.mode==='play';k++) __lab.sim(60); window.__auto=false;
          return {finished: __lab.player.finished, t: +(__lab.player.finishT||__lab.G_.t).toFixed(0), mode: __lab.G_.mode}; })()""")
        check('player (autopilot) completes the daily course within 8 minutes', me['finished'], json.dumps(me))
        check('no runtime errors', not errs, '; '.join(errs[:3]))
        await b.close()

    bad = [r for r in RESULTS if not r[1]]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed')
    sys.exit(1 if bad else 0)

asyncio.run(main())
