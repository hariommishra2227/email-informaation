# Contact Pairing Fix

## Root cause

The extractor selected names, emails, phones, company, designation, and address independently from the entire message. This allowed a name from one contact or quoted signature to be combined with another person’s email.

## Files changed

- `extractor.py`
- `tests/test_contact_pairing.py`
- `CONTACT_PAIRING_FIX.md`

No authentication, Graph, storage, synchronization, duplicate detection, UI, or export files were changed.

## Pairing algorithm

HTML is converted to readable line-preserving text. Quoted reply and forwarded history markers are removed. Current content is split into blank-line blocks; signature-like blocks containing designation/company information may be joined to their immediately following email block to preserve existing signature behavior.

Each block is extracted independently. An email is paired only with names in that block. The closest name above the email is preferred, with a score based on block locality, line distance, local-part/name similarity, and generic-mailbox status. Generic mailboxes never derive a name from their username. Low-confidence pairs retain the email but clear the name. `extract_contacts()` returns separate records for multiple blocks.

Sender-header names are only used when the sender email is the selected email and the name is compatible with it.

## Test cases

Coverage includes personal matching email, named generic sales signature, unnamed generic email, sender/signature separation, forwarded old signatures, two contacts, different blocks, initials, and unrelated usernames.

Focused result: `8 passed`.

## Before/after examples

Before:

```text
Rahul Sharma

sales@abc.com
```

Could produce `Rahul Sharma / sales@abc.com`.

After: the email is retained and the name is blank because the candidates are in different blocks.

```text
Neha Verma
Sales Manager
Acme Ltd
sales@abc.com
```

After: `Neha Verma / sales@abc.com`, with a block-local pairing confidence score.
