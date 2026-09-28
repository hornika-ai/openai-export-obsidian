# Security and privacy

OpenAI and ChatGPT exports can contain private conversations, personal data,
credentials, uploaded files, and generated artifacts. Never commit an export,
an emitted private pack, or a local analysis directory to this repository.

The public tests use synthetic fixtures only. The repository `.gitignore`
excludes the standard local input, output, cache, and secret directories, but
contributors remain responsible for reviewing every staged file before a
commit.

If you discover a vulnerability in the parser, report it privately to the
repository owner instead of opening a public issue containing a real export or
private evidence.
