-- 팽스닥 마라톤 랭킹 테이블 (Supabase → SQL Editor에 붙여넣고 Run)
-- 로그인 없이 기록: 기기마다 무작위 키를 만들고, 서버에는 그 해시만 저장해요.
create table if not exists public.scores (
  key_hash   text primary key,
  nick       text not null check (char_length(nick) between 1 and 12),
  char_id    int  not null check (char_id between 0 and 8),
  best       int  not null default 0,
  best_rank  int,
  day        date not null default (now() at time zone 'Asia/Seoul')::date,
  day_best   int  not null default 0,
  day_fin    boolean not null default false,
  runs       int  not null default 0,
  updated_at timestamptz not null default now()
);
alter table public.scores enable row level security;   -- 정책 없음: 테이블에 직접 읽기/쓰기 불가
revoke all on public.scores from anon, authenticated;

-- 공개 랭킹 조회 (키 해시 일부만 노출)
create or replace function public.get_leaderboard(p_kind text, p_day date)
returns table (pid text, nick text, char_id int, best int, best_rank int, day date, day_best int, day_fin boolean)
language sql stable security definer set search_path = public as $$
  select left(s.key_hash, 16), s.nick, s.char_id, s.best, s.best_rank, s.day, s.day_best, s.day_fin
  from public.scores s where (p_kind <> 'today' or s.day = p_day)
  order by case when p_kind = 'today' then s.day_best else s.best end desc, s.updated_at asc limit 100;
$$;
revoke all on function public.get_leaderboard(text, date) from public;
grant execute on function public.get_leaderboard(text, date) to anon, authenticated;

-- 기록 제출: 점수 범위 검사, 최고 기록만 올라감
create or replace function public.submit_score(p_key text, p_nick text, p_char int, p_score int, p_rank int, p_fin boolean, p_day date)
returns json language plpgsql security definer set search_path = public as $$
declare
  h text := encode(sha256(convert_to(p_key, 'UTF8')), 'hex');
  today date := (now() at time zone 'Asia/Seoul')::date;
  r public.scores;
begin
  if p_key is null or char_length(p_key) < 20 then raise exception 'bad key'; end if;
  if p_score is null or p_score < 0 or p_score > 6000 then raise exception 'bad score'; end if;
  if p_char is null or p_char < 0 or p_char > 8 then p_char := 0; end if;
  p_nick := left(coalesce(nullif(trim(p_nick), ''), '익명'), 12);
  if p_day is null or abs(p_day - today) > 1 then p_day := today; end if;
  insert into public.scores as s (key_hash, nick, char_id, best, best_rank, day, day_best, day_fin, runs, updated_at)
  values (h, p_nick, p_char, p_score, p_rank, p_day, p_score, coalesce(p_fin, false), 1, now())
  on conflict (key_hash) do update set
    nick = excluded.nick, char_id = excluded.char_id,
    best_rank = case when excluded.best >= s.best then excluded.best_rank else s.best_rank end,
    best = greatest(s.best, excluded.best),
    day_best = case when s.day = excluded.day then greatest(s.day_best, excluded.day_best) else excluded.day_best end,
    day_fin = case when s.day = excluded.day then (s.day_fin or excluded.day_fin) else excluded.day_fin end,
    day = excluded.day, runs = s.runs + 1, updated_at = now()
  returning * into r;
  return json_build_object('best', r.best, 'day_best', r.day_best, 'pid', left(h, 16));
end $$;
revoke all on function public.submit_score(text, text, int, int, int, boolean, date) from public;
grant execute on function public.submit_score(text, text, int, int, int, boolean, date) to anon, authenticated;
