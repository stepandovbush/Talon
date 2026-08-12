# Talon — Caspian Buildathon Demo Video Script (4:00 max)

Trimmed to roughly 480 spoken words, which sits around 3:30 at a normal pace, leaving real margin under the 4:00 cap even if you slow down on the technical terms.

---

**0:00–0:30 — Open**
*[Screen: index.html hero]*

Hello everyone! My name is Stepan Dovbush and this is Talon. Talon is an AI agent that finds the exact right person at a company and reaches out to them for you, and if that company hides behind a support bot, it escalates until a real human responds. It's built for marketers hunting for partnership contacts, and job seekers trying to find the actual hiring manager behind a listing.

**0:30–0:55 — Two channels, one handler**
*[Screen: home.html or agent.py]*

The hard requirement first, since judges check the code for it: Talon runs on two Caspian channels, email and Telegram, through one shared handler. `agent.py` holds one function, `handle_company_reply`, deciding bot or human, and both `server.py` and `main.py` call that exact same function. Nothing duplicated per channel.

**0:55–1:20 — Signup and the Gmail caveat**
*[signup.html]*

Signup is real, password hashed against my own backend, or Sign in with Google. One caveat: that OAuth app is still in Testing mode pending verification, so only allow-listed testers can complete it. If a judge wants to try it, or the Gmail connection I'll show later, send me your Google email and I'll add you in seconds.

**1:20–2:05 — The agent loop, live**
*[home.html, send a real message]*

This is the creative core. I ask Talon to find a contact, and a live panel streams every real step it's taking, driven by a callback through the research pipeline, not a canned animation. If it hits an unconfirmed company, it stops and asks which one I meant, since names collide across unrelated businesses. Once confirmed, it runs contact discovery, a leadership summary, and intent signals through a Groq reasoning layer, ranks the strongest routes, and saves it to Reports. When I ask it to send something, it never fires on its own, it hands off to Cases every time.

**2:05–2:50 — Cases: confirmation and proof it's real**
*[cases.html, pending card]*

This pending card is exactly what appeared. I set a mood, ask it to sound human, add anything else, and only then does it draft and send for real through Caspian. This isn't mocked: a background thread subscribes to Caspian's live event stream for the life of the process, and when a reply lands, that handler decides bot or human and escalates or resolves automatically. I hit a real bug here, Caspian assigns a new conversation ID to every reply instead of keeping the original thread, so my first version silently lost replies. The fix falls back to Caspian's stable customer ID, verified against a real reply mid-testing.

**2:50–3:12 — Reports, Map, Connect**
*[report.html, map.html, connect.html]*

Reports keeps every research pass permanently. Map lays companies out as a graph and rechecks each one every few hours on its own. Connect links Gmail, GitHub, Slack, and LinkedIn, GitHub with a pasted token, Slack and LinkedIn through one OAuth flow parameterized per provider.

**3:12–3:35 — Caspian, exactly, and close**
*[code or a live chat]*

Every send goes through `comm.initiate()` on Caspian's own hosted address, Telegram runs a separate `CommClient` through that same handler, and every reply gets reconciled through `events()` and `list_conversations()`. That's Talon, one agent, two real channels, one handler, built on Caspian. Thanks for watching, send me your email for tester access.
