<!-- translation-of: docs/how-to/email_channel.md sha256:93725e0d1c57 -->
**English** · [Русский](email_channel.md)

# How-to: enable the email channel (once, five minutes)

A procedure for the owner. Needed once: afterward, the system can put
a text block in an email. The reason for “email instead of Telegram” is in the docstring for
`notify.email_owner` and in `BACKLOG.md::BL-STALLED-THREADS-1`.

## What you do

1. Create an **app password** in your email account (not the main mailbox password:
   a separate one you can revoke without changing the main password).
   Gmail example: Google Account → Security → App
   passwords; two-step verification must be enabled, or the option will not appear.
2. Tell me the **email address** and the password itself. I will infer the server and port from the address.
   I will put them in secrets on Studio.
   **The cost of this step, stated explicitly:** a password sent in chat remains in
   the conversation transcript forever. The alternative is to paste one prepared line into
   Terminal yourself; then the value never leaves the machine. Your choice; an app password
   can be revoked separately from the main password either way.

That's all. The rest is my part; it is below to make the procedure complete, not for you to do it.

## What I do (to set it up again after cleanup)

Files go in the OWNER's secrets directory (`~/.health_secrets`), one value
per file, permissions 600:

    printf '%s' 'smtp.gmail.com'          > ~/.health_secrets/smtp_host
    printf '%s' '<адрес владельца>'       > ~/.health_secrets/smtp_user
    printf '%s' '<пароль приложения>'     > ~/.health_secrets/smtp_password
    chmod 600 ~/.health_secrets/smtp_*

The host is the SMTP server of the provider where you created the app password;
`smtp.gmail.com` above is an example for Gmail. The lesson that prompted this explicit statement:
an app password from one provider does not work with another provider's SMTP server. For a
`535 Invalid user or password` response, first check that the password's provider matches the
host, not the login format. An example that reads like configuration must be
real or labeled as invented. A Google app password consists of exactly
sixteen lowercase Latin letters with no separators.

Optional: `smtp_port` (default 465, implicit TLS) and `email_to` if
emails should go to a different address instead of the same mailbox.

Verification is one line; a real email will arrive:

    python3.11 -c "import notify; print(notify.email_owner('Health OS — проба канала', 'Канал работает.'))"

`True` = the server accepted it. Nothing arrived despite `True`: check spam. The machine sees nothing between
“accepted by the relay” and “in the inbox.”

## What goes through this channel and what does not

- **Goes through:** infrequent messages read in full: the weekly summary, the report on stalled
  threads. Things you return to.
  The stalled-thread report arrives **on Mondays** and lists threads with
  **more than 6 days** without a single commit. It is calculated from git history, not from the status
  a person maintains manually in the thread journal (in the private part of the project); closed threads are filtered out by
  that status. The email names only threads that crossed the threshold this week;
  threads stalled since previous weeks appear as a count, or the same four names week after
  week stop being read. Each such thread also has a card in the console, and
  it will never close by default: “finish or close” is a question with
  no meaningful default answer. The duration has one home:
  `night_cycle.STALLED_DAYS`, and a test checks it against this line.
- **Does not go through:** urgent and daily messages. They stay in Telegram. If you send that stream
  here too, one subject arrives in two voices: exactly what the channel fix addressed.

Email is a transport inside `notify.py`, not a separate sender: the “system
writing to the owner” has one home and one master switch. A precedent for the cost: August 1, two
senders with different throttles, 19278 messages.

## If the channel is not configured

`email_owner` returns `False` and writes “email channel is not configured” to stderr.
Nothing crashes, and no incomplete message is sent. Whoever requested the email must
notice `False` themselves: there is no general sensor for this, and that is an intentional boundary.
A sensor for a channel without a single producer would guard nothing.
