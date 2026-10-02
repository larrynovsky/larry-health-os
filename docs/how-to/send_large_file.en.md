<!-- translation-of: docs/how-to/send_large_file.md sha256:055ab1f64c14 -->
**English** · [Русский](send_large_file.md)

# How to send a large file to the bot

> For people who use the bot and have finished the introduction.

Send files up to 20 MB straight into the chat — with the paperclip, as a document. The bot replies
"Received "<file name>" … Processing it now." Telegram does not hand the bot files over 20 MB —
most often that is raw genome data.

A chip file (23andMe, AncestryDNA and others from the list below) — first try packing it into a
`.zip` (in Finder: right-click → "Compress"): such a text file shrinks several times, and if the
archive is under 20 MB, send it straight into the chat. Do not pack a whole genome (VCF) into a
`.zip` — the bot cannot read it from there; use a cloud for it. Anything else over 20 MB — also a
cloud link.

## As a cloud link

1. Put the file in Google Drive, Dropbox, Yandex Disk or OneDrive. The bot does not know other
   clouds and sites, including iCloud Drive and WeTransfer: this protects the machine from
   downloading anything at all.
2. Set access to "anyone with the link" (view is enough).
3. Send the link to the bot as an ordinary message.

The bot replies "I've received your link", and once downloaded — "Received "<file name>" … Processing
it now." As soon as "Received …" arrives, close link access: while it is open, anyone with the
link can download the file.

How long to wait. A chip file (23andMe, AncestryDNA and others from the list below) is processed in
a few minutes, ending with "Genome loaded: … variants" — for a chip usually hundreds of thousands.
A whole genome (VCF) takes hours.

## Which files fit

- Raw chip data: 23andMe, AncestryDNA, MyHeritage, FTDNA, tellmeGen (Starter/Advanced),
  LivingDNA. Straight in `.zip`, as the company gives it — no need to unpack.
- A whole genome in VCF: `.vcf` or compressed `.vcf.gz` (tellmeGen Ultra, Atlas and others). Do
  **not** put a VCF into a `.zip`: it is not read from an archive, unpack it. The limit is 3 GB per
  file; an uncompressed VCF may not fit, a compressed `.vcf.gz` is 4–5 times smaller.
- Raw sequencer reads (FASTQ, BAM) do not fit.

## Where the file ends up

The file is downloaded to the machine where the system runs and stays there after processing — in
your installation's data directory; it is not deleted by itself. Whoever runs that machine can read
it; if the system was set up for you on someone else's computer, that is its owner. Only they can
delete the file too.

## If something goes wrong

| What you see | What to do |
|---|---|
| "I couldn't download the file: …" | Set access to "anyone with the link" and send the link again |
| "the link opened a page instead of a file" | Same: the cloud shows a sign-in page instead of the file when access is closed |
| "… more than 3072 MB …" | Send a compressed `.vcf.gz` |
| The bot answers the link like an ordinary message and does not say "I've received your link" | The link is not from the four clouds above. Move the file there |
| "… it looks like genetic data in … format, which I cannot import yet" | The format is not on the list above. Ask the company for a "raw data" download in a supported form |
| Half an hour after "Received … Processing" for a chip (or a day for a VCF), "Genome loaded" has not arrived | Tell the person who maintains the installation or open an [issue](https://github.com/larrynovsky/larry-health-os/issues) for the developers |

The bot does not load a second genome over one already loaded, so as not to overwrite your genome
with someone else's. A whole genome (VCF) over your own chip is fine: it refines the data. If the
VCF differs strongly from the chip, the bot saves nothing and replies that it looks like another
person's genome. The bot cannot replace a loaded genome with a different one — the person who
maintains the installation does that by hand; there is no guide for it yet.

## As a file path — only in an installation without Docker

If the system runs without Docker (from a clone of the repository) and the file is on the same
machine, you can send its full path, for example `/Users/anna/Downloads/genome.zip` (in Finder:
right-click the file, hold Option → "Copy … as Pathname"). The bot replies "Received the file path".
The bot reads the path with the rights of its own account: on a machine where several people use
the bot, it can take someone else's file too.

In a Docker installation the path does not work: the container does not see the Mac's folders, and
the bot replies that it cannot see files on this computer. Use a cloud.
