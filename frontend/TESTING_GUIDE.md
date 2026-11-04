# Manual testing guide

Follow this by role. Each scenario lists what to do and what you should see. If something doesn't match, note the role, the scenario number and what you saw.

## 1. Set up

Use the throwaway test database so nothing touches your development data. The seed deletes everything in its database, and refuses to run on anything except a test database.

Open a terminal in the repository root for each block.

**Terminal 1: test services and sample data**

```
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml up -d --wait
```

**Terminal 2: everything else**

```
cd frontend
npm install
npm run build
../.venv/Scripts/python e2e/stack/run_stack.py
```

Wait for `Stack ready.`, then open http://127.0.0.1:4173. This seeds the data and starts the API, worker, fake Groq and web server. On macOS or Linux, use `../.venv/bin/python`.

**When you've finished**, press Ctrl+C in Terminal 2, then in Terminal 1 run:

```
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml down -v
```

### About the fake AI

In this setup, chat answers come from a fake Groq, which repeats the first sentence of the best matching passage. That checks the app, not the quality of AI answers. Adding one of these words to a question that matches your documents makes it behave differently:

| Add to the question | What it does |
|---|---|
| `trigger-500` | The AI service fails |
| `trigger-bad-request` | The AI service refuses the request (a 502) |
| `trigger-429` | Rate limited |
| `trigger-once` | Fails the first time, then works |
| `trigger-decline` | The AI says the passages don't answer it |
| `trigger-no-citation` | The AI cites a source that doesn't exist |
| `trigger-script` | The AI's answer contains HTML |
| `trigger-slow` | Takes 2 seconds, which makes double-clicks easy to test |

To check real answers, stop the stack and restart it with `E2E_LIVE_GROQ=1` and `GROQ_API_KEY` set. The trigger words then do nothing, and each question uses your Groq quota.

### Reset between runs

Scenarios change the data. To start again, press Ctrl+C in Terminal 2 and run `run_stack.py` again. It rebuilds the database.

### Finding IDs for URLs

The isolation checks need real IDs. Open `frontend/e2e/.seed.json` after the stack starts. Its `ids` section lists each one, such as `globex`, `globex_sales` and `doc_pricing`. Where this guide says `{globex}`, use that number.

## 2. Accounts

The password for all of them is `Demo-Pass-123!`. It exists only in the seed.

| Person | Email | Tenant | Team and role |
|---|---|---|---|
| Ava | `ava.admin@acme.example.com` | Acme Corp, tenant admin | All teams, through being tenant admin |
| Mark | `mark.manager@acme.example.com` | Acme Corp | People Ops, manager |
| Mia | `mia.member@acme.example.com` | Acme Corp | People Ops, member |
| Vic | `vic.viewer@acme.example.com` | Acme Corp | People Ops, viewer |
| Eli | `eli.engineer@acme.example.com` | Acme Corp | Engineering, member |
| Nia | `nia.newcomer@acme.example.com` | Acme Corp | No team, with a grant on one document |
| Sam | `sam.shared@example.com` | Acme Corp and Globex Inc | People Ops, member. Sales, manager. |
| Bo | `bo.admin@globex.example.com` | Globex Inc, tenant admin | All teams, through being tenant admin |
| Ben | `ben.member@globex.example.com` | Globex Inc | Sales, member |

### Sample documents

| Document | Team | Contains |
|---|---|---|
| `leave-policy.txt` | Acme, People Ops | 24 days of leave, five may carry forward. Shared with Nia. |
| `salary-bands.txt` | Acme, People Ops | Salary bands, labelled confidential |
| `incident-runbook.txt` | Acme, Engineering | Incident paging rules |
| `deploy-guide.txt` | Acme, Engineering | Embedding has failed, ready for you to retry |
| `globex-pricing.txt` | Globex, Sales | Enterprise pricing |
| `support-tiers.txt` | Globex, Support | Support response times |

## 3. Everyone: signing in and errors

| # | Do this | Expect |
|---|---|---|
| 3.1 | Sign in as Mia with the wrong password | "Incorrect email or password." You stay on the sign-in page. |
| 3.2 | Sign in as Mia | You reach Acme Corp. The header shows `Tenant: Acme Corp`, `Tenant member` and `People Ops · Member`. |
| 3.3 | Reload the page | You stay signed in. |
| 3.4 | Sign out, then press Back | You're on the sign-in page, and no data shows. |
| 3.5 | While signed out, open `/t/{acme}/documents`, then sign in | You land on the Documents page, not the home page. |
| 3.6 | Sign in, close the tab, and open the app in a new tab | You have to sign in again. |
| 3.7 | Create an account at `/register` with a new email | You see "You aren't part of a tenant yet" and a form to create one. |
| 3.8 | Register again with Mia's email | "Email already registered". |
| 3.9 | Stop the API (or turn off your network), then click a menu item | A friendly "couldn't reach the server" message with Try again, and no raw error. |

## 4. Tenant admin: Ava

Sign in as Ava.

**You should see:** `Tenant admin` in the header, People in the menu, all Acme teams and documents, and New team, Edit details and Edit team.

**You shouldn't see:** anything from Globex.

| # | Do this | Expect |
|---|---|---|
| 4.1 | Open People | All seven Acme people, each with a role and user ID. No Globex-only people. |
| 4.2 | Add `no-such-person@example.com` | "We couldn't add that person. Check that they've created an account with this email." |
| 4.3 | Add the account you registered in 3.7, as Member | A success message, and they appear in the list. |
| 4.4 | Teams → New team, create "Test team" | It appears in the list. |
| 4.5 | Create a second team with the same name | "A team with this name already exists" or a similar plain message. |
| 4.6 | Open Test team → Deactivate, then Reactivate | A warning that the team is inactive appears, then goes away. |
| 4.7 | Open People Ops | Your access shows as "via tenant admin". You can change roles and remove people, and the role list includes Admin. |
| 4.8 | Overview → Edit details, change the description | It's saved, and the new text shows on the Overview. |
| 4.9 | Open `leave-policy.txt` | "Shared with" lists Nia by name. |
| 4.10 | Documents: filter by Team = Engineering | Only Engineering documents. |
| 4.11 | Add Department = HR with Team = All teams, and press Enter | Only `leave-policy.txt` and `salary-bands.txt`. |
| 4.12 | Set Status = Failed as well | "No documents match these filters." |

## 5. Team manager: Mark

Sign in as Mark.

**You should see:** `People Ops · Manager`, Edit team, Deactivate, member controls, "Shared with" on People Ops documents.

**You shouldn't see:** People in the menu, Engineering, Admin as a role choice.

| # | Do this | Expect |
|---|---|---|
| 5.1 | Teams | Only People Ops. New team is disabled, with "Only tenant admins can create teams." |
| 5.2 | Open `/t/{acme}/members` directly | "Not found or you don't have access." |
| 5.3 | Open People Ops | The role list offers Manager, Member and Viewer, but not Admin. |
| 5.4 | Type Eli's user ID (see `.seed.json`, `eli`), choose Viewer, and add | Eli appears as a Viewer. You type an ID because only tenant admins can list everyone. |
| 5.5 | Change Eli to Member | "Role updated." |
| 5.6 | Remove Eli and confirm | Eli leaves the list. |
| 5.7 | Type Bo's user ID and add | "User is not a member of this tenant". |
| 5.8 | Open `salary-bands.txt` → Shared with, give access to Eli | Eli is listed, with Revoke. |
| 5.9 | In a private window, sign in as Eli and open that document's URL | Eli can read it, but sees no "Shared with" section. |
| 5.10 | Back as Mark, Revoke and confirm. Eli reloads. | Eli sees "Not found or you don't have access." |
| 5.11 | Open the Engineering team's URL | "Not found or you don't have access." |

## 6. Member: Mia

Sign in as Mia.

**You should see:** Upload document, People Ops documents, chat and search.

**You shouldn't see:** People, "Shared with", member controls, or Engineering documents.

### Upload

| # | Do this | Expect |
|---|---|---|
| 6.1 | Upload document | The page states the allowed types and the 25 MB limit. |
| 6.2 | Choose a `.exe` file (or rename any file) | "That file type isn't supported…" and Upload stays disabled. |
| 6.3 | Choose a text file over 25 MB | "That file is larger than the 25 MB limit." |
| 6.4 | Rename a text file to `fake.pdf` and upload | "File content is not a valid PDF". |
| 6.5 | Enter `not json` in Custom details, and upload a valid file | "Enter this as JSON…" |
| 6.6 | Enter `{"tenant_id": 2}` in Custom details | A message that this reserved field can't be set. |
| 6.7 | Upload a normal text file with a department and tags | The document page opens. Its status moves through waiting and processing to "Ready to search". While it's in progress, you see "This document isn't searchable yet." |
| 6.8 | Upload the same file again | "This team already has this file", with a link to it. |
| 6.9 | Search for words from the new file | The file appears in the results. |

### Search

| # | Do this | Expect |
|---|---|---|
| 6.10 | Search "annual leave" | `leave-policy.txt`, with a match score and a snippet. Selecting the title opens the document. |
| 6.11 | Search with Department = "Nonexistent" | "No matching passages in the documents you can read." |
| 6.12 | Search "incident on-call paging" | No `incident-runbook.txt`, because it belongs to Engineering. |

### Chat

| # | Do this | Expect |
|---|---|---|
| 6.13 | New conversation, ask "How many days of annual leave do employees get?" | An answer with a numbered source, and the title becomes your question. |
| 6.14 | Select the number or the source below | A window shows the passage, its document and a link to it. |
| 6.15 | Reload | The conversation is still there. |
| 6.16 | Ask "Can unused days be carried forward?" | A second answer. |
| 6.17 | Ask "What is the capital of Australia?" | "Your documents don't seem to cover this." |
| 6.18 | Ask the leave question, adding `trigger-decline` | "The passages I found don't answer this." |
| 6.19 | Ask the leave question, adding `trigger-no-citation` | "I couldn't back up an answer with a source." The made-up claim isn't shown. |
| 6.20 | Ask the leave question, adding `trigger-500` | "The AI service is unavailable, try again." and Try again. |
| 6.21 | Ask the leave question, adding `trigger-bad-request` | The same message. |
| 6.22 | Ask the leave question, adding `trigger-429` | "Too many requests, wait a moment." |
| 6.23 | Ask the leave question, adding `trigger-once`. When it fails, select Try again. | The answer appears, and the question appears only once. |
| 6.24 | Ask the leave question, adding `trigger-slow`, and double-click Send | One question and one answer. |
| 6.25 | Ask the leave question, adding `trigger-script` | The answer shows `<img…` and `<script>` as ordinary text. No broken image, and nothing runs. |
| 6.26 | Rename the conversation | The new title shows in both the header and the list. |
| 6.27 | Archive it | The message box is replaced by a note, and it moves to the Archived tab. Restore brings it back. |
| 6.28 | New conversation, "Search in" = People Ops only | A badge says "People Ops documents only". |

## 7. Viewer: Vic

Sign in as Vic.

**You should see:** `People Ops · Viewer`, People Ops documents, search and chat.

**You shouldn't see:** a working Upload button, "Shared with", member controls.

| # | Do this | Expect |
|---|---|---|
| 7.1 | Documents | Upload is disabled, with "Viewers can read documents but can't upload them." |
| 7.2 | Open `/t/{acme}/documents/upload` directly | "You can't upload here…" with no form. |
| 7.3 | Open `leave-policy.txt` and select Download | The file downloads. |
| 7.4 | Ask the leave question in chat | An answer with a source. |

## 8. Person with a grant only: Nia

Sign in as Nia.

**You should see:** `No team yet`, and only `leave-policy.txt` in Documents.

| # | Do this | Expect |
|---|---|---|
| 8.1 | Open `leave-policy.txt` | She can read it, with no "Shared with" section. |
| 8.2 | Open the `salary-bands.txt` URL (`/t/{acme}/teams/{acme_hr}/documents/{doc_salary}`) | "Not found or you don't have access." |
| 8.3 | Teams | "You aren't in a team yet…" |

### Losing access

| # | Do this | Expect |
|---|---|---|
| 8.4 | As Mark, open `salary-bands.txt` and give access to Nia, typing her user ID from `.seed.json` (`nia`) | She's listed as "User ID" and her number, because managers can only see names of people in their team. |
| 8.5 | As Nia, in chat, ask "What is the salary band for senior engineers?" | An answer citing `salary-bands.txt`. |
| 8.6 | As Mark, revoke it. As Nia, reload the conversation. | "This answer's sources are no longer available to you." The salary figure and the source are gone. |
| 8.7 | As Mark, share it with Nia again. Nia reloads. | The answer comes back. |

## 9. Person in two tenants: Sam

Sign in as Sam.

| # | Do this | Expect |
|---|---|---|
| 9.1 | Look at the tenant switcher | Acme Corp and Globex Inc. |
| 9.2 | In Acme, open Documents | People Ops documents only. |
| 9.3 | Switch to Globex Inc | The header changes to `Tenant: Globex Inc` and `Sales · Manager`. No Acme documents appear, not even briefly. |
| 9.4 | Open Documents | `globex-pricing.txt` only. |
| 9.5 | Press Back until you return to Acme | Acme's data, with nothing from Globex. |
| 9.6 | In Globex, open Sales | Sam can manage it (Edit team is available). |
| 9.7 | In Acme, open People Ops | Sam can't manage it. Edit team is disabled, with a reason. |
| 9.8 | Start a chat in Globex, then switch to Acme | The Globex conversation isn't listed in Acme. |

## 10. Isolation checks

These matter most. Each one must show **"Not found or you don't have access."** and none of the content. A wrong answer here is a security problem, so report it first.

| # | Signed in as | Open this URL | Why |
|---|---|---|---|
| 10.1 | Mia | `/t/{globex}` | Another tenant |
| 10.2 | Mia | `/t/{globex}/documents` | Another tenant's documents |
| 10.3 | Mia | `/t/{globex}/teams/{globex_sales}/documents/{doc_pricing}` | Another tenant's document |
| 10.4 | Ava (admin) | `/t/{globex}/teams/{globex_sales}/documents/{doc_pricing}` | Being an admin stops at the tenant |
| 10.5 | Ava | `/t/{acme}/teams/{globex_sales}/documents/{doc_pricing}` | A real document reached through the wrong tenant |
| 10.6 | Ava | `/t/{acme}/teams/{acme_hr}/documents/2147483000` | Doesn't exist. It must look the same as 10.5. |
| 10.7 | Ava | `/t/2147483000` | A tenant that doesn't exist. It must look the same as 10.1. |
| 10.8 | Ava | `/t/{acme}/teams/{globex_sales}` | Another tenant's team |
| 10.9 | Eli | `/t/{acme}/teams/{acme_hr}/documents/{doc_salary}` | Another team's document |
| 10.10 | Eli | `/t/{acme}/teams/{acme_hr}` | Another team |
| 10.11 | Vic | `/t/{acme}/members` | Tenant admins only |
| 10.12 | Ava | The URL of a conversation Mia created | Conversations are private, even from admins |
| 10.13 | Ben | `/t/{acme}/chat/1` | A conversation in another tenant |

Also check that data can't leak through search and chat:

| # | Do this | Expect |
|---|---|---|
| 10.14 | As Ava, search Acme for "enterprise pricing per month" | No `globex-pricing.txt`, and no "5000 dollars". |
| 10.15 | As Eli, search for "annual leave" | No `leave-policy.txt`. |
| 10.16 | As Eli, ask chat "How many days of annual leave do employees get?" | No "24 days", and no `leave-policy.txt`. The fake AI may answer from an Engineering document, because a short document can pass the relevance threshold. Any source shown must belong to Engineering. A real model should decline instead. |

## 11. Retrying a failed embedding: Eli

| # | Do this | Expect |
|---|---|---|
| 11.1 | As Eli, open `deploy-guide.txt` | "Embedding failed", an explanation, and Retry embedding. |
| 11.2 | Search for "deployments every Tuesday" before retrying | `deploy-guide.txt` isn't in the results. |
| 11.3 | Select Retry embedding | The status moves on to "Ready to search", and the button goes away. |
| 11.4 | Search again | `deploy-guide.txt` is in the results. |

## 12. Known gaps to expect

These aren't failures of this test run:

- **People in the menu:** only tenant admins have it, and managers type user IDs, because no API lets them list the tenant.
- **Non-admin tenant role:** the header says "Tenant member", because no API returns the exact tenant role.
- **Removing someone from a tenant:** there's no option, because the backend has no endpoint for it.
- **Deactivating a tenant:** it isn't offered, because a known backend bug would lock the admin out.
- **Sign out:** a copied token still works until it expires, because the backend has no logout endpoint.
- **Failed processing:** there's no retry when the file couldn't be read, only for failed embeddings, because that's the only retry the backend offers.
- **Delete:** there's no way to delete documents, because delete management is out of scope for v1.
