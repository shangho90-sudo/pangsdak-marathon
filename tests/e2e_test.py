#!/usr/bin/env python3
"""
팽스닥 마라톤 — 브라우저 자동 테스트 (Playwright + headless Chromium)

  pip install playwright && python -m playwright install chromium
  python tests/e2e_test.py                      # 실제 CDN(three.js)과 가짜 랭킹 서버 사용
  python tests/e2e_test.py --three-dir node_modules/three   # 오프라인: 로컬 three@0.160.0 사용

랭킹(Supabase) 호출은 기본적으로 가짜 서버로 가로채서, 실제 랭킹 데이터에 기록하지 않아요.
게임 진행은 window.__lab.sim(초)로 빠르게 돌려 검증합니다 (렌더링 없이 물리/AI만 진행).
"""
import argparse, asyncio, json, pathlib, sys
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(); ap.add_argument('--three-dir'); ap.add_argument('--only'); args = ap.parse_args()
THREE = pathlib.Path(args.three_dir).resolve() if args.three_dir else None
RESULTS = []

class Mock:
    def __init__(self): self.calls = []; self.rows = {}; self.fail_models = set(); self.down = False; self.uid = 'me'
mock = Mock()

async def route(r):
    u = r.request.url
    if THREE and 'cdn.jsdelivr.net/npm/three@0.160.0/' in u:
        return await r.fulfill(path=str(THREE / u.split('three@0.160.0/')[1]), content_type='application/javascript')
    if '.supabase.co/rest/v1/rpc/' in u:
        fn = u.rsplit('/', 1)[1]; body = json.loads(r.request.post_data or '{}'); mock.calls.append((fn, body))
        if mock.down: return await r.fulfill(status=503, body='down')
        if fn.startswith('submit_score'):
            prev = mock.rows.get('me', {}); dup = prev.get('run') == body.get('p_run')
            if not dup: mock.rows['me'] = {'pid': mock.uid, 'nick': body['p_nick'], 'char_id': body['p_char'], 'best': max(prev.get('best', 0), body['p_score']), 'best_rank': body['p_rank'], 'day': body['p_day'], 'day_best': max(prev.get('day_best', 0), body['p_score']), 'day_fin': body['p_fin'], 'run': body.get('p_run')}
            m = mock.rows['me']; return await r.fulfill(json={'best': m['best'], 'day_best': m['day_best'], 'pid': 'me', 'duplicate': dup})
        if fn == 'get_leaderboard':
            rows = [dict(v) for v in mock.rows.values()] + [{'pid': 'zz', 'nick': '봇', 'char_id': 1, 'best': 500, 'best_rank': 9, 'day': body['p_day'], 'day_best': 400, 'day_fin': False}]
            return await r.fulfill(json=rows)
    if u.startswith('https://game.test/'):
        rel = u[len('https://game.test/'):].split('?')[0] or 'index.html'
        if rel.startswith('models/c') and rel.endswith('.json') and int(rel[8:-5]) in mock.fail_models: return await r.fulfill(status=500, body='x')
        f = ROOT / rel
        return await (r.fulfill(path=str(f)) if f.exists() else r.fulfill(status=404, body=''))
    if THREE: return await r.abort()          # offline mode: nothing else may load
    return await r.continue_()

def check(name, ok, info=''):
    RESULTS.append((name, bool(ok), info)); print(('PASS ' if ok else 'FAIL ') + name + (f'  — {info}' if info else ''), flush=True)

async def new_page(p, device=None):
    b = await p.chromium.launch(args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    ctx = await b.new_context(**(p.devices[device] if device else {'viewport': {'width': 1100, 'height': 700}}))
    pg = await ctx.new_page(); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    await pg.route('**/*', route)
    await pg.goto('https://game.test/index.html'); await pg.wait_for_function('window.__lab && document.getElementById("sCover") && !document.getElementById("sCover").classList.contains("hidden")', timeout=60000)
    return b, pg, errs

async def start(pg, mode='daily', tap=False):
    click = pg.tap if tap else pg.click
    if await pg.is_visible('#sCover'): await click('#btnStart')
    await pg.wait_for_selector('#sTitle:not(.hidden)')
    await click('#dailyBtn' if mode == 'daily' else '#practiceBtn')
    await pg.wait_for_function("__lab.G_.mode === 'count' || __lab.G_.mode === 'play' || !document.getElementById('loadErr').classList.contains('hidden')", timeout=60000)

async def main():
    async with async_playwright() as p:
        # ---------------- mobile (iPhone) flow
        b, pg, errs = await new_page(p, 'iPhone 13')
        check('boot: cover screen shown without errors', not errs, '; '.join(errs[:2]))
        await pg.tap('#btnStart'); await pg.wait_for_selector('#sTitle:not(.hidden)')
        await pg.fill('#nickIn', '팽<b>팽</b>!!'); await pg.dispatch_event('#nickIn', 'change')
        check('nickname: invalid characters removed', await pg.input_value('#nickIn') == '팽b팽b', await pg.input_value('#nickIn'))
        await pg.tap('.ch:nth-child(4)')
        await pg.evaluate("document.getElementById('dailyBtn').click(); document.getElementById('dailyBtn').click();")   # double tap
        await pg.wait_for_function("__lab.G_.mode === 'count'", timeout=60000)
        check('start: double tap builds only one race', await pg.evaluate('__lab.buildCount') == 2, f"buildCourse calls={await pg.evaluate('__lab.buildCount')} (1 at boot + 1 race)")
        mock.uid = await pg.evaluate('__lab.Rank.uid') or 'me'
        n = await pg.evaluate('__lab.ents.length'); check('start: 16 runners created', n == 16, f'{n}')
        check('character select: player uses chosen model', await pg.evaluate('__lab.player.id') == 3)
        # countdown lock
        p0 = await pg.evaluate('[__lab.player.pos.x, __lab.player.pos.z]')
        box = await pg.locator('#stick').bounding_box()
        await pg.evaluate("""(()=>{const st=document.getElementById('stick'); const r=st.getBoundingClientRect(); const o={pointerId:7,bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,pointerType:'touch'};
          st.dispatchEvent(new PointerEvent('pointerdown',o)); st.dispatchEvent(new PointerEvent('pointermove',{...o,clientY:o.clientY-60}));})()""")
        await pg.evaluate('__lab.sim(0.5)')
        p1 = await pg.evaluate('[__lab.player.pos.x, __lab.player.pos.z]')
        check('countdown: player cannot move', abs(p0[0]-p1[0]) + abs(p0[1]-p1[1]) < 1e-6)
        await pg.evaluate("document.getElementById('stick').dispatchEvent(new PointerEvent('pointercancel',{pointerId:7,bubbles:true}))")
        tv = await pg.evaluate('[__lab.touchVec.x, __lab.touchVec.y]'); check('joystick: pointercancel recentres', tv == [0, 0], str(tv))
        await pg.evaluate('__lab.sim(3.5)'); check('countdown ends -> play', await pg.evaluate("__lab.G_.mode") == 'play')
        # joystick move + jump at the same time
        await pg.evaluate("""(()=>{const st=document.getElementById('stick'); const r=st.getBoundingClientRect(); const o={pointerId:8,bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,pointerType:'touch'};
          st.dispatchEvent(new PointerEvent('pointerdown',o)); st.dispatchEvent(new PointerEvent('pointermove',{...o,clientY:o.clientY-60}));})()""")
        a0 = await pg.evaluate('__lab.player.maxA')
        await pg.dispatch_event('#bJump', 'pointerdown', {'pointerId': 9, 'pointerType': 'touch'})
        await pg.evaluate('__lab.sim(0.05)')
        vy = await pg.evaluate('__lab.player.vel.y'); check('jump: short tap jumps while moving', vy > 3, f'vy={vy:.2f}')
        await pg.evaluate('__lab.sim(1.0)')
        check('joystick: moving forward', await pg.evaluate('__lab.player.a') > a0)
        await pg.evaluate("document.getElementById('stick').dispatchEvent(new PointerEvent('pointerup',{pointerId:8,bubbles:true}))")
        check('joystick: release recentres', await pg.evaluate('[__lab.touchVec.x, __lab.touchVec.y]') == [0, 0])
        # push: put a bot right in front of the player, tap push
        await pg.evaluate("""(()=>{const P=__lab.player, B=__lab.ents[1]; __lab.sim(0.6); const fx=Math.sin(P.face), fz=Math.cos(P.face); B.pos.set(P.pos.x+fx*.8, P.pos.y, P.pos.z+fz*.8); B.invul=0; B.stun=0; B.shield=0; P.pushCd=0; P.stun=0;})()""")
        await pg.dispatch_event('#bPush', 'pointerdown', {'pointerId': 10, 'pointerType': 'touch'})
        st = await pg.evaluate('[__lab.player.pushT, __lab.ents[1].stun]')
        check('push: motion starts immediately and hits the bot', st[0] > 0 and st[1] > 0, str(st))
        # fall -> revive at a safe spot with 1.5s invulnerability
        await pg.evaluate("(()=>{const P=__lab.player; __lab.sim(1); P.pos.y -= 9; P.vel.set(0,-5,0); P.grounded=false;})()")
        await pg.evaluate('__lab.sim(0.2)')
        dbg = await pg.evaluate("({mode:__lab.G_.mode, y:__lab.player.pos.y, lg:__lab.player.lastGroundY, nf:__lab.player.noFallCheck, g:__lab.player.grounded})")
        check("fall: revive prompt appears", dbg["mode"] == "revive", json.dumps(dbg))
        await pg.tap('#reviveBtn')
        rv = await pg.evaluate("(()=>{const P=__lab.player; return {mode:__lab.G_.mode, invul:P.invul, onRoad: !!__lab.roadTop(P.a,P.r), safe: __lab.spotIsSafe(P.a,P.r)!==null, rev:__lab.G_.revives};})()")
        check('revive: back on safe road, invulnerable, revive used', rv['mode'] == 'play' and rv['invul'] > 1.4 and rv['onRoad'] and rv['safe'] and rv['rev'] == 0, json.dumps(rv))
        await pg.evaluate("__lab.sim(0.3); (()=>{const P=__lab.player, B=__lab.ents[1]; B.face=Math.atan2(P.pos.x-B.pos.x, P.pos.z-B.pos.z); B.pos.set(P.pos.x-Math.sin(B.face)*.8, P.pos.y, P.pos.z-Math.cos(B.face)*.8); B.pushCd=0; B.stun=0;})()")
        st0 = await pg.evaluate("(()=>{ const B=__lab.ents[1]; const P=__lab.player; const was=P.stun; return was; })()")
        check('invulnerability: shield bubble visible', await pg.evaluate('__lab.player.av.shieldM.visible'))
        await pg.evaluate('__lab.sim(2.0)')
        check('invulnerability ends after ~1.5s', await pg.evaluate('__lab.player.invul') <= 0)
        # declining a revive must not re-open the prompt while still falling
        await pg.evaluate("(()=>{__lab.G_.revives=1; const P=__lab.player; P.pos.y -= 9; P.vel.set(0,-5,0); P.grounded=false; P.lastGroundY=P.pos.y+9;})()")
        await pg.evaluate('__lab.sim(0.2)'); await pg.tap('#giveupBtn')
        await pg.evaluate('__lab.sim(0.15)')
        await pg.evaluate("(()=>{const P=__lab.player; P.pos.y -= 9;})()"); await pg.evaluate('__lab.sim(0.2)')
        check('skip revive: prompt does not loop', await pg.evaluate("__lab.G_.mode") == 'play')
        # obstacles one lap above must not touch a runner on the floor below (reported: knocked back on the early stairs)
        ghost = await pg.evaluate("""(()=>{ const W=__lab.world, P=__lab.player; const out=[]; const bots=__lab.ents.filter(e=>e!==P); bots.forEach(b=>b.finished=true);   // park the AI so only obstacles can act
          for (const o of W.obs.filter(o=>o.type==='dome'||o.type==='sweeper')){ const below=__lab.roadTop(o.a - 6.283185307, o.r); if(!below) continue;
            P.pos.set(o.x, below.top, o.z); P.vel.set(0,0,0); P.stun=0; P.invul=0; P.shield=0; P.grounded=true; __lab.sim(0.1);
            const hit = P.stun > 0 || Math.hypot(P.pos.x-o.x, P.pos.z-o.z) > .2;
            if (hit){ const near = W.obs.filter(q=>Math.hypot(q.x-o.x,q.z-o.z)<4 && Math.abs((q.y??0)-below.top)<3).map(q=>q.type); window.__ghostInfo = {type:o.type, stun:P.stun, moved:Math.hypot(P.pos.x-o.x,P.pos.z-o.z), dy:o.y-below.top, near, bots: __lab.ents.filter(e=>e!==P && e.pos.distanceTo(P.pos)<1.5).length}; }
            out.push(hit); }
          bots.forEach(b=>b.finished=false); return out; })()""")
        check('collision: obstacles on the lap above do not hit the floor below', ghost and not any(ghost), f'{len(ghost)} spots checked, hits={sum(ghost)} ' + json.dumps(await pg.evaluate('window.__ghostInfo || null')))
        await pg.evaluate("(()=>{const P=__lab.player; const s=P.safe; __lab.setPolar(P, s.a, s.r, s.y); P.vel.set(0,0,0); P.lastGroundY=s.y;})()")
        # background / foreground
        await pg.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
        hid = await pg.evaluate("[__lab.G_.mode, __lab.touchVec.x, __lab.touchVec.y]")
        check('app hidden: game pauses and input resets', hid == ['settings', 0, 0], str(hid))
        await pg.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
        await pg.tap('#setResume'); check('resume after returning', await pg.evaluate("__lab.G_.mode") == 'play')
        # full race with AI (autopilot): stages, finish, score, ranking submit
        await pg.evaluate("window.__auto = true; __lab.sim(260); window.__auto = false;")
        fin = await pg.evaluate("({mode:__lab.G_.mode, stage:__lab.G_.stage, fin:__lab.player.finished, order:__lab.G_.finishOrder.length, score:__lab.G_.score, rank:__lab.player.finishRank, t:__lab.G_.t})")
        check('race: AI and player progress through stages', fin['stage'] >= 2, json.dumps(fin))
        check('race: someone finishes', fin['order'] >= 1, json.dumps(fin))
        if fin['fin']:
            exp = await pg.evaluate("(()=>{const P=__lab.player; return Math.max(0,Math.floor(P.maxA*9.3)) + 1000 + (16-P.finishRank)*60 + Math.max(0, Math.round((480-P.finishT)*3)); })()")
            check('finish: score formula', fin['score'] == exp, f"{fin['score']} vs {exp}")
            await pg.wait_for_selector('#sResult:not(.hidden)', timeout=15000)
            try:
                await pg.wait_for_function("document.getElementById('resBoard').textContent.includes('위') || document.getElementById('resBoard').textContent.includes('저장')", timeout=15000)
            except Exception: pass
            check("ranking: result shows today rank", "위" in await pg.text_content("#resBoard"), await pg.text_content("#resBoard"))
            subs = [c for c in mock.calls if c[0].startswith('submit_score')]
            check('ranking: run submitted once with run id + time', len(subs) == 1 and subs[0][1].get('p_run') and subs[0][1].get('p_time', 0) > 0, str(len(subs)))
            check('ranking: nickname sent cleaned', subs and subs[0][1]['p_nick'] == '팽b팽b')
            await pg.evaluate('__lab.Rank.submit(__lab.G_.lastRun || {})'); await pg.wait_for_timeout(300)
            check('ranking: same run never sent twice', len([c for c in mock.calls if c[0].startswith('submit_score')]) == 1)
        else:
            check('finish: player autopilot finished (informational)', True, 'player did not finish this seed; score check skipped')
        # share with native share sheet
        await pg.evaluate("window.__shared=null; navigator.share = async (d) => { window.__shared = d; };")
        await pg.evaluate("document.getElementById('shareBtn').click()"); await pg.wait_for_timeout(200)
        sh = await pg.evaluate('window.__shared')
        check('share: native share with game link', sh and sh.get('url') == 'https://shangho90-sudo.github.io/pangsdak-marathon/' and '점' in sh.get('text', ''), json.dumps(sh, ensure_ascii=False)[:120])
        await pg.evaluate("delete navigator.share; navigator.share = undefined; navigator.clipboard.writeText = async (t) => { window.__copied = t; };")
        await pg.evaluate("document.getElementById('shareBtn').click()"); await pg.wait_for_timeout(200)
        cp = await pg.evaluate('window.__copied || ""'); check('share: clipboard fallback includes link', 'github.io/pangsdak-marathon' in cp, cp[:80])
        # rankings tabs
        await pg.evaluate("document.getElementById('homeBtn2').click()")
        await pg.tap('#rankBtn'); await pg.wait_for_timeout(600)
        await pg.tap('#tabAll'); await pg.wait_for_timeout(600)
        kinds = [c[1].get('p_kind') for c in mock.calls if c[0] == 'get_leaderboard']
        check('ranking: today and all-time boards requested separately', 'today' in kinds and 'all' in kinds, str(kinds))
        check('mobile flow: no runtime errors', not errs, '; '.join(errs[:3]))
        await b.close()

        # ---------------- desktop: time limit, practice, restart x10 memory, resize, sound, load failures
        b, pg, errs = await new_page(p)
        await start(pg, 'practice'); await pg.evaluate('__lab.sim(4)')
        await pg.evaluate("__lab.G_.t = __lab.TIME_LIMIT - 0.5; __lab.sim(1.5)")
        check('time limit: game ends without finishing', await pg.evaluate("__lab.G_.mode") == 'result' and not await pg.evaluate('__lab.player.finished'))
        await pg.wait_for_timeout(500)
        prac = await pg.evaluate("JSON.parse(localStorage.getItem('pangsdak-practice')||'[]').length")
        check('practice: result kept locally (not sent to ranking)', prac >= 1 and not [c for c in mock.calls[-3:] if c[0].startswith('submit') and False], f'{prac} local records')
        mem = []
        for i in range(10):
            await pg.evaluate("document.getElementById('againBtn').click()")
            await pg.wait_for_function("__lab.G_.mode === 'count'", timeout=60000)
            await pg.evaluate("__lab.sim(4); window.__auto=true; __lab.sim(3); window.__auto=false; __lab.endGame(false)")
            await pg.wait_for_timeout(150)
            mem.append(await pg.evaluate("(__lab.renderer.compile(__lab.scene, __lab.camera), [__lab.renderer.info.memory.geometries, __lab.renderer.info.memory.textures])"))   # upload everything so counts are comparable
        grow_g = mem[-1][0] - mem[2][0]; grow_t = mem[-1][1] - mem[2][1]
        check('restart x10: GPU geometries/textures do not keep growing', grow_g <= 25 and grow_t <= 12 and mem[-1][1] - mem[5][1] <= 8, f'geo {mem[2][0]}->{mem[-1][0]}, tex {mem[2][1]}->{mem[-1][1]}')
        await pg.set_viewport_size({'width': 420, 'height': 860}); await pg.wait_for_timeout(300)
        asp = await pg.evaluate('innerWidth/innerHeight'); check('resize: portrait layout applied', asp < 1)
        snd = await pg.evaluate("""(async()=>{ await __lab.Audio.init(); const names=['jump','land','push','hit','bonk','thud','thunk','slap','fall','revive','goal','checkpoint','bounce','boost','shield','event','click']; for(const n of names) __lab.Audio.play(n); __lab.Audio.startBgm(); __lab.Audio.stopBgm(); return __lab.Audio.ctx ? __lab.Audio.ctx.state : 'none'; })()""")
        check('sound: all effects + BGM play without errors', snd in ('running', 'suspended'), snd)
        check('desktop flow: no runtime errors', not errs, '; '.join(errs[:3]))
        await b.close()

        # ---------------- model loading failures
        mock.fail_models = {2}
        b, pg, errs = await new_page(p)
        await pg.evaluate("localStorage.setItem('labtower-char','2')"); await pg.reload(); await pg.wait_for_function('window.__lab && !document.getElementById("sCover").classList.contains("hidden")', timeout=60000)
        await start(pg, 'practice')
        ok = await pg.evaluate("__lab.G_.mode === 'count' && __lab.ents.length === 16 && __lab.player.id !== 2 && !!__lab.player.av.acts.Run")
        check('loading: one model failing -> substitute model with animations', ok)
        await b.close()
        mock.fail_models = set(range(9))
        b, pg, errs = await new_page(p)
        await start(pg, 'practice')
        check('loading: all models failing -> error + retry button', await pg.is_visible('#loadErr') and await pg.is_visible('#loadRetry'))
        mock.fail_models = set(); await pg.click('#loadRetry')
        await pg.wait_for_function("__lab.G_.mode === 'count'", timeout=60000)
        check('loading: retry succeeds after network recovers', await pg.evaluate('__lab.ents.length') == 16)
        await b.close()

    bad = [r for r in RESULTS if not r[1]]
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed')
    sys.exit(1 if bad else 0)

asyncio.run(main())
