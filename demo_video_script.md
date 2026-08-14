# Talon — Caspian Buildathon Demo Video Script

A full, natural-sounding walkthrough of every feature: chat, reports, map, connections, and case history. Read it like you're actually talking to someone, not reciting copy.

---

**Open**
*[Screen: index.html hero]*

Hello everyone! My name is Stepan Dovbush and this is Talon. Talon is an AI agent that helps you find the emails and social media accounts a company hides behind its support bot, and it can generate and send the actual outreach emails and messages, connecting with platforms like Gmail, GitHub, Slack, and more along the way. Talon is built for marketers who are looking to secure partnerships, sponsorships, or some type of advertisement for their company, and for job seekers who want to skip the black hole of a job listing and reach the actual hiring manager on the other end of it.

**Intro page, signup, onboarding**
*[index.html, click through to signup.html, then onboarding.html]*

So here's how someone actually gets into Talon. This landing page sends new users to a signup page, where you can create an account with a normal email and password, or sign in with Google. From there, you're walked through a short onboarding slideshow, four real screenshots of Talon actually working, so before you've even touched the product, you already know what it's going to do for you. One thing I do want to mention honestly: my Google sign in is still sitting in Google's Testing status while verification is pending, so right now only people I've specifically allow-listed can use that button. If any judge wants to try it, or the Gmail connection I'll show you in a bit, just send me your Google email and I'll add you as a tester in seconds.

**Demo time**
*[home.html]*

Here I am going to show you a demo of Talon! I'm going to go through every part of it: the chat itself, the reports it saves, the map it builds, the accounts it connects to, and the case history it keeps as it works.

**The chat feature**
*[home.html, send a real message]*

I'll start by just talking to it. I ask Talon to find me a contact, and instead of a loading spinner, a live panel opens up on the side and streams every real step it's taking as it happens, checking the company's site, searching for their social profiles, reading up on their leadership. If I mention a company it hasn't confirmed yet in this conversation, it doesn't just guess, it stops and shows me exactly which company it thinks I mean, with the real domain and a short description, and waits for me to confirm, because plenty of company names belong to more than one totally unrelated business. Once it's confirmed, it puts together contact discovery, a leadership summary, and intent signals like hiring or funding activity, and it ranks the strongest ways to actually reach someone there. And if I ask it to send a message to someone, it never just fires it off. Every single time, it hands that off to Cases, which is exactly what I want to show you next.

**The history feature: Cases**
*[cases.html, pending card]*

This is Cases, and this pending card is exactly what shows up the moment I ask Talon to send something. I get to set the mood I want, tell it to sound like an actual person instead of corporate copy, add any other instructions, and only after I approve it does Talon actually draft and send the message for real through Caspian. And every message it sends becomes a tracked case right here, with its full history, when it was opened, whether it's still waiting on a reply, and when the next follow up is scheduled to go out automatically if nobody's responded. In the background, a thread stays subscribed to Caspian's live event stream the entire time this app is running, and the moment a real reply comes back, that same handler reads it and decides whether it's a canned bot response or an actual person, and either escalates automatically or marks the case resolved. I actually hit a real bug building this part: Caspian gives every single reply a brand new conversation ID instead of keeping it on the original thread, so my first version was silently losing replies without me knowing. The fix falls back to Caspian's stable customer ID instead, and I proved it worked against a real reply that came back while I was testing.

**The report feature**
*[report.html]*

Every company Talon looks into gets saved here permanently as a full report, the contacts, the leadership summary, the intent signals, and the ranked contact routes, so it's never just something that scrolls away in a chat, it's something you can always come back to.

**The map feature**
*[map.html]*

The map takes all of those reports and lays them out as one connected picture, showing how companies relate to each other and suggesting a next step for each one. And it doesn't just sit there, a background process rechecks every company every few hours on its own, so if something new happens, new hiring, new funding, it shows up here even if I never go looking for it myself.

**The connection feature**
*[connect.html]*

And this is Connect, where I link outside accounts in. GitHub just needs a personal access token pasted in, no approval process at all. Slack and LinkedIn both go through a real OAuth flow that I built once and reused for both of them. And Gmail connects here too, for reading a bit of personal inbox context, that's the same Google app I mentioned earlier that's still in Testing status, so again, send me your email if you want to try it yourself.

**Close**

That's Talon: it researches who to actually talk to, personalizes exactly how it reaches them, waits for your approval before it ever sends anything, and then keeps working the conversation, on email and on Telegram, until a real person responds, all running on Caspian the whole way through. Thanks for watching.
