<!-- translation-of: docs/how-to/connect_cloud_assistants.md sha256:c270d5c60d61 -->
**English** · [Русский](connect_cloud_assistants.md)

# How to connect Health OS to Claude and ChatGPT

Once connected, Claude and ChatGPT (in the browser and on the phone) answer health questions from
Health OS data: lab results, a day of device metrics, the problem list, long-term memory facts. Every
tool answer carries its source and date; when there is no data, the assistant receives "no data" and
must not fill the gap. Why it works this way — [intent](../explanation/mcp_gateway.md); what each
tool does — [reference](../reference/mcp_tools.md).

**Who this is for.** Right now the server is built only in the owner's installation on Studio
(`scripts/install.py --owner-override`). An installation that follows the [first install tutorial](../tutorials/first_install.md)
does not include it.

**What leaves the machine.** Tool answers go to Anthropic (Claude) and OpenAI (ChatGPT) — whoever
asked. Name, date of birth and document paths are cut out of the answers; medical data is not.

## 1. Allow a separate node in Tailscale

The server is exposed to the internet by its own tailnet node `health-mcp` through Funnel, so the public
address opens neither the dashboard nor any other service on the machine. In the Tailscale access
policy (admin console → Access controls) add an owner for the tag and the Funnel permission:

```json
"tagOwners": { "tag:mcp": ["autogroup:admin"] },
"nodeAttrs": [ { "target": ["tag:mcp"], "attr": ["funnel"] } ]
```

It is worth adding a test that the node cannot reach your other machines (replace the addresses with yours):

```json
"tests": [ { "src": "tag:mcp", "deny": ["studio:22", "studio:8001", "studio:8002"] } ]
```

## 2. Turn the server on at Studio

```bash
mkdir -p ~/.health_mcp
docker volume create health_mcp-ts
cd ~/health_scripts && bash scripts/deploy_container.sh
```

The `~/.health_mcp` directory is the switch: while it exists, re-rendering the settings adds the
services `mcp` (the server) and `mcp-funnel` (the tailnet node). On first start the node asks to join the
tailnet — the link is in the log:

```bash
docker --context colima-health logs health-mcp-funnel-1 2>&1 | grep -m1 login.tailscale.com
```

Open the link, sign in with your account and approve the node in the admin console. The server address
is `https://health-mcp.<your-tailnet>.ts.net/mcp`. The public DNS record sometimes does not appear right away.

## 3. Connect Claude

In Claude settings add a custom connector with the server address from step 2. Claude opens the page
"Подключение к Health OS"; a six-digit code arrives in Telegram from the Health OS bot. Enter it on the
page. A connector added in the browser works in the mobile app too.

## 4. Connect ChatGPT

In ChatGPT a custom connector is added in developer mode (connector settings). Same address, same
sign-in: the code from Telegram.

## 5. Check

Ask the assistant: "check the connection to Health OS". The `ping` tool answers "Health OS на связи."
Then ask something you know the answer to, for example "which lab tests do I have". New tools appear in a
new chat: a chat that is already open knows only what existed when it started.

## If the code "expired" or did not arrive

- A code lives 10 minutes; after five wrong attempts the request is burned — start connecting again.
- The code did not arrive in Telegram — sign-in refuses by itself ("could not send the code"): check that
  the bot is running.

## How to close access

Turn the server off entirely (reversible):

```bash
docker --context colima-health stop health-mcp-funnel-1
```

Revoke every issued token — the assistants will have to sign in again:

```bash
docker --context colima-health exec health-mcp-1 python3 -c "import mcp_auth, os, pathlib; print(mcp_auth.Store(pathlib.Path(os.environ['HEALTH_DATA_DIR']) / 'data' / 'mcp_auth.sqlite').revoke_all())"
```

There is no revoke command in the bot yet.
