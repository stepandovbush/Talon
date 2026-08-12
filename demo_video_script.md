# Talon — Caspian Buildathon Demo Video Script (4:00 max, confident cut)

Rewritten to sound like what it is: a genuinely capable autonomous agent, not a chatbot with an email button. 512 spoken words, about 3:56 at a normal pace.

---

**0:00–0:30 — Open**
*[Screen: index.html hero]*

Hello everyone! My name is Stepan Dovbush and this is Talon. Talon is an AI agent that finds the exact right person at a company and reaches out for you, and when that company hides behind a support bot, it escalates until a real human responds. Built for marketers hunting the one partnership contact that matters, and job seekers cutting straight to the hiring manager behind a listing.

**0:30–1:00 — Intro page, signup, onboarding**
*[index.html, click through to signup.html, then onboarding.html]*

To begin, an intro page sends users to sign up, then into an onboarding slide show. Signup takes an email and password, or Sign in with Google, and onboarding walks through four real screenshots of Talon working before dropping you into the product. One note: that Google app is still in Testing status, so only allow-listed testers can complete it. Judges who want to try it, send me your Google email and you're in seconds.

**1:00–1:25 — Two channels, one handler**
*[Screen: home.html or agent.py]*

The hard requirement, proven not claimed: Talon runs on two Caspian channels, email and Telegram, through one shared brain. `agent.py` holds one function, `handle_company_reply`, the only place a bot-or-human call gets made, and both `server.py` and `main.py` call it, no duplicated logic per channel.

**1:25–2:10 — The agent loop, live**
*[home.html, send a real message]*

Watch this, this is where Talon stops looking like a chatbot. I ask it to find a contact, and a live panel streams every real decision, a callback firing through the research pipeline, not an animation. Hit an unconfirmed company and it stops and asks which one I meant, a wrong guess wastes a full pass. Once confirmed, it runs contact discovery, a leadership summary, and intent signals through a Groq layer, ranks the strongest routes, and files it to Reports unasked. Ask it to send something and it holds, every time, it hands to Cases first.

**2:10–2:55 — Cases: confirmation, and proof this is real**
*[cases.html, pending card]*

This pending card is exactly what came up. I set a mood, tell it to sound human, add anything else, and only then does it draft and send for real. Not a demo trick: a background thread stays subscribed to Caspian's live event stream, and when a reply lands, that handler decides bot or human and escalates or resolves on its own. A real bug hit here: Caspian mints a new conversation ID on every reply, so my first version silently dropped replies. The fix falls back to Caspian's stable customer ID, proved against a real reply mid build.

**2:55–3:17 — Reports, Map, Connect**
*[report.html, map.html, connect.html]*

Reports holds every research pass permanently. Map lays companies out as a graph and rechecks each one every few hours on its own. Connect wires in Gmail, GitHub, Slack, and LinkedIn, GitHub with a pasted token, Slack and LinkedIn through one OAuth flow reused across both.

**3:17–3:40 — Caspian, exactly, and close**
*[code or a live chat]*

Every send leaves through `comm.initiate()` on Caspian's hosted address, Telegram runs its own `CommClient` through that same handler, and every reply gets reconciled through `events()` and `list_conversations()`. That's Talon: one agent, two real channels, one brain, built on Caspian. Thanks for watching.
