# Cymor MovieBox Test API

Minimal Express app to test whether `moviebox-js-sdk` (aoneroom/MovieBox API)
works reliably from a free-tier host like Render.

## Files

- `package.json` — dependencies (express + moviebox-js-sdk)
- `index.js` — test server with `/search` and `/detail` routes

## How to deploy (phone-only workflow)

1. Create a new GitHub repo, e.g. `cymor-moviebox-test`
2. Upload `package.json` and `index.js` to the **root** of the repo
   (Render free tier needs files at root — same as movie-hub before)
3. Go to Render → New → Web Service → connect this repo
4. Settings:
   - Build Command: `npm install`
   - Start Command: `npm start`
   - Plan: Free
5. Deploy and wait for it to go live

## How to test from your phone browser

Once deployed, visit (replace with your actual Render URL):

```
https://your-app.onrender.com/health
```
Should return `{"status":"healthy", ...}`

Then try:

```
https://your-app.onrender.com/search?q=avatar
```

### What to look for

- **Works + returns movie list** → great, move to `/detail?path=<one of the detailPath values>`
  to confirm stream URLs come back too
- **500 error mentioning network/timeout/ECONNREFUSED** → Render's IP is likely
  blocked by aoneroom, same pattern as YouTube blocking CymorTune. Next step:
  try Railway instead, or set `MOVIEBOX_API_HOST` env var to `movieboxapp.in`
  and redeploy
- **403 / Cloudflare challenge in the error** → aoneroom has bot protection;
  may need the `MOVIEBOX_API_PROXY` env var with a working proxy, or this
  becomes a dead end and we fall back to the WebTorrent approach

## If /search works

Copy a `detailPath` from the JSON response and hit:

```
https://your-app.onrender.com/detail?path=titanic-m7a9yt0abq6
```

This should return stream URLs (mp4/HLS). If those URLs play directly in a
browser `<video>` tag, you've got your backend — wire this into Cymor Movie
Hub's existing frontend, replacing the WebTorrent.js layer.
