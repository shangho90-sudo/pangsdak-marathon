#!/usr/bin/env python3
"""
팽스닥 마라톤 — Runway로 배경음/효과음 생성
------------------------------------------------
사용법 (터미널):
    export RUNWAYML_API_SECRET=key_...      # Runway 개발자 포털에서 발급한 키
    pip install requests
    python3 tools/generate_audio_runway.py   # game/audio/*.mp3 생성

생성 결과를 game/audio/ 에 두면 게임이 자동으로 이 파일을 사용합니다.
(파일이 없으면 게임 안의 합성 사운드로 대신 재생됩니다.)

예상 비용(프로젝트 크레딧): 배경음 90초 ≈ 30 크레딧, 효과음 13개 ≈ 25 크레딧.
"""
import os, sys, time, pathlib, requests

API = "https://api.dev.runwayml.com"
KEY = os.environ.get("RUNWAYML_API_SECRET")
if not KEY:
    sys.exit("RUNWAYML_API_SECRET 환경변수를 먼저 설정하세요.")
H = {"Authorization": f"Bearer {KEY}", "X-Runway-Version": "2024-11-06",
     "Content-Type": "application/json"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "audio"
OUT.mkdir(exist_ok=True)

MUSIC = {
    "bgm": dict(
        promptText=("Upbeat bouncy chiptune-pop loop for a casual 3D platformer where cute "
                    "chibi doctors race up a spiral road around a stadium tower in a sports festival. 128 BPM, playful "
                    "synth lead, plucky bass, light drums, rising tension, seamless loop, "
                    "no vocals."),
        duration=90, instrumental=True),
}
SFX = {
    "jump":       ("cartoon jump boing, short springy whoosh up, game sound effect", 0.6),
    "land":       ("soft cartoon footstep landing thud on wooden platform, game sfx", 0.5),
    "push":       ("quick cartoon shove whoosh with a playful 'hup' energy, game sfx", 0.6),
    "hit":        ("comic bonk hit, character knocked back, rubbery impact, game sfx", 0.6),
    "fall":       ("cartoon falling whistle descending pitch, long slide down, game sfx", 1.6),
    "revive":     ("magical sparkle rising chime, character respawn power up, game sfx", 1.2),
    "goal":       ("triumphant short fanfare with sparkles, level complete jingle", 2.5),
    "checkpoint": ("bright two-note bell chime, checkpoint reached, game sfx", 0.9),
    "bounce":     ("rubbery cartoon bumper boing, character bounced away, game sfx", 0.6),
    "boost":      ("quick whooshing speed boost zip with rising pitch, game sfx", 0.8),
    "shield":     ("magical bubble shield pop and shimmer, game sfx", 0.7),
    "event":      ("short arcade alert jingle announcing a surprise event, game sfx", 1.2),
    "click":      ("soft UI button click pop, game menu", 0.3),
    "crash":      ("heavy wooden crate slamming onto the ground, dusty thud impact, game sfx", 0.8),
    "boing":      ("big springy launch ring boing with rising whoosh, game sfx", 0.8),
}

def create(endpoint, body):
    r = requests.post(f"{API}/v1/{endpoint}", json=body, headers=H, timeout=60)
    r.raise_for_status()
    return r.json()["id"]

def wait(task_id):
    while True:
        t = requests.get(f"{API}/v1/tasks/{task_id}", headers=H, timeout=60).json()
        if t["status"] == "SUCCEEDED":
            return t["output"][0]
        if t["status"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"task {task_id} {t['status']}: {t.get('failure')}")
        time.sleep(5)

def save(name, url):
    data = requests.get(url, timeout=120).content
    (OUT / f"{name}.mp3").write_bytes(data)
    print(f"  ✓ {name}.mp3  ({len(data)//1024} KB)")

jobs = []
for name, body in MUSIC.items():
    jobs.append((name, create("text_to_music", {"model": "eleven_music_v2_5", **body})))
for name, (prompt, dur) in SFX.items():
    jobs.append((name, create("sound_effect", {"model": "eleven_text_to_sound_v2",
                                               "promptText": prompt,
                                               "duration": max(0.5, dur)})))
print(f"{len(jobs)}개 작업 제출 — 완료 대기 중…")
for name, tid in jobs:
    try:
        save(name, wait(tid))
    except Exception as e:  # keep going; the game falls back to synth for missing files
        print(f"  ✗ {name}: {e}")
print("완료 →", OUT)
