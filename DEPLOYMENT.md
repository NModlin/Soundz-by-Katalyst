# Billy Bassline Music Manager

The repository now includes a private manager at `/manager.html` and a public playlist API at `/api/playlist`.

## Safety rules

- Every import is stored as `draft`.
- Nothing is public until an authenticated manager user presses **Publish**.
- Unpublish returns a track to draft state.
- Removing a playlist entry removes only its metadata; the staged recording remains on disk.
- Only WAV and MP3 files with matching file signatures are accepted.
- Manager endpoints fail closed unless `MUSIC_ADMIN_TOKEN` is configured.

## Deploy

`render.yaml` is the checked-in deployment configuration. On the service, set `MUSIC_ADMIN_TOKEN` to a long random secret and keep the persistent disk mounted at `/var/data`. The app uses `/var/data/music` for metadata and audio by default in that configuration.

The current repository has no evidence of an existing production deployment or configured provider. After deployment, verify `/api/health`, set the token, and open `/manager.html` over HTTPS. Do not put the admin token in the repository or in a public URL.
