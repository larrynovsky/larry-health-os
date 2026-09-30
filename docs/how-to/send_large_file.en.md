<!-- translation-of: docs/how-to/send_large_file.md sha256:54a234aebbb4 -->
**English** · [Русский](send_large_file.md)

# How to send a large file to the bot

> Document type: How-to (Diátaxis). For you if you have already installed the system and completed onboarding
> (installation tutorial, step 8). The parser for submitted files (`com.larry.health.lab-intake`) is running.

Telegram does not let the bot retrieve files larger than 20 MB. Send such a file — most often raw genome data —
as a cloud link or a path to a file on the machine where the bot runs. The file is then
parsed just like one sent in chat.

## Send a cloud link

1. Put the file in Google Drive, Dropbox, Yandex Disk, or OneDrive. The bot does not recognize other clouds,
   including iCloud Drive. You do not need to extract a `.zip` archive — the parser
   will look inside it on its own.
2. Enable “anyone with the link” access (viewing access is enough).
3. Send the link to the bot as a regular message.

The bot replies “Link received”, and after downloading, “Received ‘<file name>’. Parsing.”
For a genome, you will receive “Genome loaded: … variants” a few minutes later.

As soon as “Received …” arrives, you can turn off link access: the file is already on your machine.

## Send a path to a file on this computer

If the bot runs on the same machine as the file, send its full path, for example
`/Users/вы/Downloads/genome.zip`. In Finder, right-click the file, hold Option →
“Copy … as Pathname”. The bot replies “Path received”; the rest works just like a link.

The path is read with the permissions of the user running the bot: the bot can retrieve any file on that machine,
not just yours. If you are the bot's only user, this changes nothing. On a machine where several people
use the bot, this requires deliberate trust between them.

## If something goes wrong

| Bot response | What to do |
|---|---|
| “Could not retrieve the file: file not found or link access is closed” | Check “anyone with the link” access and send the link again |
| “The cloud returned a page instead of a file” | Same as above: Google shows a login page instead of the file if access is closed |
| “The file is larger than 3072 MB” | An uncompressed full genome may not fit: send `.vcf.gz` (compressed VCF, 4–5 times smaller) |
| The bot responds to a link from another site as a regular message and does not say “Link received” | The bot downloads only from the four clouds above — this protects the machine. Move the file to one of them |
| The genome format is not supported | Raw data from 23andMe, AncestryDNA, MyHeritage, FTDNA, tellmeGen (Starter/Advanced), and LivingDNA is supported. A full genome in VCF (`.vcf` or `.vcf.gz`, tellmeGen Ultra, “Atlas”) is parsed; it takes hours. A VCF inside a zip is not accepted — unzip it. Raw reads (FASTQ) are not parsed |

The bot does not load a second genome over an existing one, to avoid accidentally overwriting it with someone else's.
You can replace a genome only manually. A full genome (VCF) over your own chip is allowed: it
refines the genotypes. If the VCF noticeably disagrees with the chip (more than 0.5% of shared
positions), the bot writes nothing and replies that this looks like another person's genome.
