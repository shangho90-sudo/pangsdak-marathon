# 🏃 팽스닥 마라톤

빙글빙글 도는 탑을 감고 올라가는 16인 장애물 레이스 (모바일 웹게임, three.js).

- 플레이: GitHub Pages 주소로 접속 — 로그인 필요 없음
- 랭킹: Supabase (`supabase.sql` 실행 후 `config.js`에 URL·anon 키 입력)
- 캐릭터: Tripo로 만든 모델을 Blender에서 애니메이션(대기·달리기·점프·밀치기·낙하) 추가 후 웹용으로 경량화
- 사운드: `tools/generate_audio_runway.py`로 Runway에서 생성한 mp3를 `audio/` 폴더에 넣으면 자동 사용 (없으면 내장 합성음)
