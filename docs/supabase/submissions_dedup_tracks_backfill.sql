create extension if not exists pgcrypto;

update public.tracks
set client_track_id = gen_random_uuid()
where client_track_id is null;
