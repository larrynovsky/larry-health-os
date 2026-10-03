<!-- translation-of: docs/how-to/add_labs.md sha256:b612bf47863e -->
**English** · [Русский](add_labs.md)

# How to add lab results

> For people who already use the Telegram bot. You do not need a terminal to send reports.
> Behaviour checked against the code on 2026-10-02; Docker installation limits are below.

Download the report from the laboratory's website or photograph the paper report, send it
to the bot, then check the recognised results against the original. **“Received” means
the file is saved; its values have not yet been added to your results.** You complete
the review and add the values on your own page; the link arrives in your bot chat.

## 1. Prepare your reports

- The best option is the original PDF from the laboratory's website with selectable
  text. Scanned PDFs are also accepted. Send a multi-page PDF as one file;
  the default limit is **20 pages**. A longer file is not partially recognised:
  the bot asks you to split it into parts of at most 20 pages. Keep the date and
  test name on them and send the parts as separate files.
- Photograph the whole paper report, straight and in good light, without glare. The
  date, test names, numbers, units and reference ranges must be readable. Photograph
  each page separately and send it **as a file, without compression**, rather than as
  a regular chat photo. JPG/JPEG and PNG work. TIFF is accepted, but only the first
  frame of a multi-page TIFF is read: save its pages as a PDF or separate images instead.
- HEIC is listed as an input format, but the standard Docker image lacks the
  converter it needs. Save the photo as JPG/PNG or PDF. HEIF is not accepted by
  the incoming-file parser.
- Excel (`.xlsx`, `.xls`) and CSV are not automatically imported as lab results. Save
  the table as a PDF without cutting off columns, or get the laboratory's own PDF.
- **Extract ZIP archives of lab reports yourself.** Automatic archive extraction
  handles genome data rather than lab reports; it does not help bypass the report size limit.

The standard image has text recognition configured for **Russian and English**.
For documents in another language, ask the person maintaining the system to check
support; the model's ability to read a language does not guarantee report intake.

“For the last 3 years” on the home card is guidance for building a history, not an
upload requirement. Start with what you have: one recent report is enough for your
first upload. Older reports can also be sent; the test date must be visible on the report.

## 2. Send a file to your bot

1. Open the chat with the bot you used for onboarding.
2. Tap the paperclip → File (the name depends on your Telegram app), and choose the
   PDF or image. Send separate files for multiple paper pages.
3. Wait for the receipt. For example, for a PDF named `lab.pdf`:
   «Received “lab.pdf”. I'll extract the lab results and send them for confirmation;
   I'll also extract diagnoses and medications from the doctor's report and send
   them as cards for confirmation.» The doctor's-report addition is standard receipt
   text; it does not mean diagnoses or medications were found in your report.

A regular chat photo enters the lab pipeline only if intake for these photos has
been enabled separately. The bot may then reply «Looks like a lab report — queued
for processing. I'll extract the results and send them for confirmation.». This mode
is off by default on a new installation: a model's reply to a photo does not mean
lab results have been uploaded. Send photos as files.

**File larger than 20 MB:** put the PDF or image itself in Google Drive, Dropbox,
Yandex Disk or OneDrive, enable “anyone with the link” access, and send the link
as a regular message. The bot does not download links directly to a laboratory account.

Reply: «I've received your link. I'll download and process the file like any other
document (genome data, lab results or a medical report), then send you the result in
a separate message.» Then, for example: «Received “lab.pdf” (25.0 MB). Processing it now.».
You can close link access after that. The download limit for links is 3072 MB;
the 20-page PDF limit still applies. See [how to send a large file](send_large_file.md).
In Docker, a file path on your computer is usually invisible to the bot: use a file
in chat or a cloud link.

## 3. Wait for recognition and check the results

Processing runs in the background: the service checks incoming files once a minute
and waits at least 30 seconds after a file is saved. Two models then read each page.
Allow a few minutes for a short report; this is guidance, not a guaranteed deadline.
More pages, other queued files or a model service failure increase the wait.

Read rows first go into a separate review queue (`staging`). Agreement between the
models can give them the `auto` label; uncertainty gives them `pending`. **Even `auto`
needs your confirmation:** waiting does not count as agreement or add values to your
database or reports.

The processing result goes **to you in your own bot chat**: the filename, number of
recognised values and a link to your review sheet at
`/lab-review/<run_id>?tenant=<tenant>&show=waiting`. Ordinary confirmation needs no operator.
With several reports, the home **Lab results** card shows **waiting for you** and the
value count; **Check recognised values** opens the oldest unfinished report. After
adding its values, the card leads to the next report.

1. Open the link on the computer running the system. For an installation following
   the [first-install tutorial](../tutorials/first_install.md), the dashboard is at
   `http://127.0.0.1:8001`. On a phone, that address means the phone itself: open the
   page on the computer or use private access already configured through Tailscale.
2. Keep the original report beside it. Check names, numbers, units and reference
   ranges; look for missing rows. The date, specimen and `<`/`>` sign are not yet
   shown on the sheet, so this interface cannot confirm whether those fields are
   correct. Mark a doubtful row **Incorrect**; there is no field for correcting a number.
3. By default, the sheet shows all unfinished rows, including `auto`, `pending`,
   `review` and `gold`. **All rows in this report** also shows finished and rejected rows.
   **Check** checks whether values can be added, but already saves marked errors and
   assigned names. **Add to database**, with your confirmation, transfers eligible,
   non-rejected rows from the whole report, including rows hidden by the filter.
   Then refresh the home page.

**“🧪 Lab tests”** on a document-type card confirms only **the report type**, not the
recognised values. Values need separate confirmation on the review sheet. Opening
the sheet or staying silent adds nothing. Unknown names, conflicts and specialised
panels may need a separate decision; technical reasons for blocking rows are described
in [the lab row review queue](lab_review_queue.md).

## 4. Check that results appeared

- In the dashboard, open **Lab results** (`/labs`) and refresh the page. It shows the
  latest values by test and the last 50 results from the main database. This is a
  viewing page; use the home Lab results card for the review queue.
- Send `/labs` to the bot. It shows selected key tests from the last two years and
  other tests from the latest visit within the last 90 days, rather than the entire
  archive. «No lab results yet.» alone does not prove an old report was lost.
- Accepted data may inform subsequent morning reports. A report does not have to
  list every test and does not replace checking the dashboard table.

Specialised tests are stored separately and may not appear on `/labs`. If a test is
missing after review, ask the person maintaining the system to check its route;
resending the same file does not complete the transfer.

## If something goes wrong

| Reply or situation | What to do |
|---|---|
| «I couldn't download your file. Please send it again when you're ready — I don't have a copy yet.» | Send the file again |
| «I received your file but couldn't save it. Please send it again when you're ready — it won't be saved until you do.» | Try again; if the reply repeats, pass it to the person maintaining the system |
| «“lab.pdf” is over 20 MB, so Telegram won't let me download it. Zip it or send a file link from Google Drive, Dropbox, Yandex Disk or OneDrive with link access enabled. You can do this later — analysis will wait until I can access the file.» | For lab reports, choose a link to the PDF itself: ZIP archives of reports are not parsed |
| «I've saved “lab.xlsx”. I can't read this format automatically yet, so its contents won't be added to your records on their own.» | Save Excel/CSV as a readable PDF and send it |
| «I've already received this file — skipping the duplicate.» | A file with the same content is not parsed again, even if renamed. Check the queue and results; ask the person maintaining the system for recognition to be rerun |
| «I've already received this image — I won't process it twice.» | This reply applies when regular-photo intake is enabled. A new photo of the same sheet may pass as a new file; flag the repeat during review |
| «I've received your file. Processing is paused for now — I've reported the problem, and your file is saved.» | The file is saved. Pass the message to the person maintaining the system: the wording does not guarantee automatic repair of your installation |
| A link does not download, or the cloud returns a page instead of a file | Enable “anyone with the link” access to the file itself and send the link again |
| The report is unclear, or no results or review link follow the receipt | Get the original PDF or photograph the sheet again and send a new file. The bot sends one message for this file asking for the original PDF or a clearer photo; later polls do not repeat it |
| The review sheet does not open on your phone | Open it on the computer running the system, or ask for private access through Tailscale |
| The review sheet is empty, or nothing appears after review | Refresh the home page: it opens the oldest unfinished report. Check it and click “Add to database”. A saved file and `auto` do not yet mean results have been added; doubtful or blocked rows need a separate decision |
| PDF longer than 20 pages | The bot asks you to split it into parts of 20 pages or fewer; no rows from the long file are added |
