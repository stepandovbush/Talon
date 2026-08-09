import os

from caspian_sdk import CommClient
from dotenv import load_dotenv

import agent
import contact_finder
import llm
import state

load_dotenv()

client = CommClient()  # reads CASPIAN_API_KEY from env / .env

email_conn = client.connect_email()
EMAIL_CONNECTION_ID = email_conn.get("connection_id") or email_conn.get("id")
print(f"Connected email as connection {EMAIL_CONNECTION_ID}")

TELEGRAM_CONNECTION_ID = None
telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
if telegram_token:
    telegram_conn = client.connect_telegram(bot_token=telegram_token)
    TELEGRAM_CONNECTION_ID = telegram_conn.get("connection_id") or telegram_conn.get("id")
    print(f"Connected telegram as connection {TELEGRAM_CONNECTION_ID}")
else:
    print(
        "TELEGRAM_BOT_TOKEN not set, skipping Telegram. Message @BotFather on "
        "Telegram, run /newbot, and put the token it gives you in .env to "
        "enable it."
    )


@client.on_message
def handle(message):
    if message.channel == "telegram":
        handle_telegram(message)
    elif message.channel == "email":
        handle_email(message)


def handle_telegram(message):
    text = (message.text or "").strip()
    if not text:
        return

    request = llm.classify_request(text)
    company = request.get("company")
    recipient = request.get("recipient")
    contacts = None

    # No recipient given directly, but we know who they mean: look them up.
    if not recipient and company:
        message.typing()
        contacts = contact_finder.find_contacts(company)
        recipient = llm.pick_recipient(contacts, text)

    # Pure lookup request, or outreach that never resolved to a recipient:
    # answer with what was found instead of sending anything.
    if request.get("intent") == "lookup" or not recipient:
        if contacts is None and company:
            contacts = contact_finder.find_contacts(company)
        if contacts and (contacts["emails"] or contacts["socials"] or contacts["socials_unverified"]):
            message.reply(llm.format_contact_summary(contacts))
        elif company:
            message.reply(f"I couldn't find any public contact info for {company}.")
        else:
            message.reply(
                "I couldn't tell who to look up in that message. Tell me "
                'the company, for example: "find the partnership email for '
                'Acme" or "my ISP, support@acme.com, my bill is wrong."'
            )
        return

    draft = llm.draft_outreach(text, recipient)

    message.reply(f"On it. Emailing {recipient} now, I'll let you know the moment someone responds.")

    result = client.initiate(EMAIL_CONNECTION_ID, recipient, draft["email_text"])
    email_conversation_id = result.get("conversation_id") or result.get("id")

    state.create_case(
        email_conversation_id=email_conversation_id,
        telegram_conversation_id=message.conversation_id,
        recipient=recipient,
        opening_email=draft["email_text"],
        channel="telegram",
        subject=draft.get("subject"),
    )


def handle_email(message):
    agent.handle_company_reply(client, message)


if __name__ == "__main__":
    print("Talon is listening...")
    client.listen(ack="On it, one moment...")
