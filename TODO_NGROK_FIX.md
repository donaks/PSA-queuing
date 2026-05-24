# TODO - Fix ngrok not working

- [x] Add proper reverse-proxy handling (X-Forwarded-Proto/For) so Flask builds correct scheme and can log real client IP.
- [x] Add /api/health endpoint (public) to verify ngrok is reaching the server.
- [x] Add /api/debug-client endpoint (public) to show request headers, detected scheme, and remote_addr.
- [x] (If needed) Update README/ngrok instructions with correct local port and required headers.
- [x] Test locally with curl against /api/health and /api/state.
- [ ] Test through ngrok with browser and curl against /display and /api/state.
