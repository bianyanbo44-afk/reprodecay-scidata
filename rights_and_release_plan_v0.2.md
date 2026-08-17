# Rights and release plan v0.2

This plan separates public identifiers and derived factual records from cached
third-party source archives. It is a release-engineering checklist, not legal
advice.

## Intended released content

- normalized DOI, repository, release, tag, commit and checksum identifiers;
- project, snapshot, dependency, syntax-derived symbol, structure, delta and
  validation tables;
- relative source evidence paths and line locations;
- construction code, schemas, controlled vocabularies, data dictionary and
  figure source data;
- selection and endpoint-resolution audit records.

## Content not redistributed by default

- downloaded Zenodo, Figshare and GitHub source archives;
- full source files, documentation or licence text;
- code snippets or full function/class bodies;
- GitHub avatars, screenshots, issue/PR content or contributor personal data;
- access tokens, request headers, local cache paths or workstation metadata.

Source archives remain accessible from their original persistent archive DOI or
immutable commit URL and are verified by released checksums.

## Field-level provenance and rights rationale

Bibliographic identifiers, dates, repository URLs, commit identifiers,
checksums, file paths, package names and normalized parser outputs are released
as factual/provenance records. Raw dependency declarations and syntactic
signatures are short evidence strings derived from openly licensed source
packages; before deposition, the final field-level audit must confirm that the
chosen data licence is compatible with the intended jurisdiction and repository.

Repository licence metadata is evidence about the source package, not the
licence of this derived dataset. Each project record therefore retains the
upstream SPDX identifier and archive/repository locator.

## Selected release licences

- Derived tabular dataset: Creative Commons Attribution 4.0 International
  (`CC-BY-4.0`), limited to the original selection, organization and derived
  records described in `LICENSE-DATA`.
- Custom extraction and validation code: MIT License, as recorded in
  `LICENSE-CODE`.
- Manuscript text and figures: publisher/repository terms as applicable.

These licences do not cover third-party archives or upstream projects. Author
or institutional legal review can still be added if required by the depositing
institution, but it does not change the technical package boundary.

## Privacy and secrets audit

Before every public release:

1. scan all text and archives for tokens, credentials, cookies and local paths;
2. confirm that tables contain no email addresses or contributor profiles;
3. verify that source evidence is relative and does not embed user directories;
4. inspect repository metadata fields for unexpected personal data;
5. record scanner versions, findings and remediation in `validation_records` or
   a separate release audit.

## Repository package

The version-controlled code and derived release are published at
`https://github.com/bianyanbo44-afk/reprodecay-scidata`, with the immutable
software release tagged `v0.2.3`. Use a DOI-minting generalist or institutional
repository for the formal dataset record and archive the tagged GitHub release
to obtain a persistent code citation. The public dataset record
should include the seven core tables, manifests, schemas, data dictionary,
selection/audit ledgers, checksums, source-data CSV and licence files. Archive
code separately or in a clearly versioned subpackage and create a persistent
code citation.

## Pre-deposition gates

- final eligible census and exclusion ledger frozen;
- final endpoint snapshot date frozen;
- independent semantic audit completed and reported;
- clean-room reconstruction completed;
- field-level rights review completed;
- secrets/privacy scan completed;
- data/code licences selected by the author;
- dataset and code versions tagged;
- anonymous reviewer link tested without login;
- DOI placeholders replaced only after public/reserved identifiers exist.
