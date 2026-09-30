<!-- translation-of: NOTICE.md sha256:7842e27f6b8e -->
**English** · [Русский](NOTICE.md)

# License and third-party data

The project's code is distributed under the Apache License 2.0 — full text in `LICENSE` (owner's decision,
2026-09-23). The terms below concern other parties' data, not the code.

## Third-party data and their terms

The project uses external reference sources. This section says which of them may be distributed together with the code.
The normative list of paths that do NOT go to the public repository is `publication_zones.yaml`.

| Source | What is in the project | Terms | Goes into the publication |
|---|---|---|---|
| NCI CTCAE v5.0 / v6.0 | xlsx snapshots and JSON derived from them (`data/norm_docs/ctcae_v*.xlsx`, `ctcae_lab_v*.json`); the term map `ctcae_lab_terms*.json` is our own work (term → analyte), without codes | NCI text is free of copyright; NCI asks to be credited as the source ([cancer.gov](https://www.cancer.gov/policies/copyright-reuse)). But the files carry MedDRA codes, and MedDRA is licensed by ICH/MSSO — the terms for those codes have not been checked | document and snapshot — no: the installer downloads CTCAE from NCI itself (`scripts/install.py --fetch-ctcae`, owner's decision 2026-09-25); term map — yes |
| EFLM Biological Variation Database | API snapshot (`data/norm_docs/eflm_*`) | «You may not, except with our express written permission, distribute or commercially exploit the content» ([biologicalvariation.eu/disclaimer](https://biologicalvariation.eu/disclaimer), read 2026-09-23). When used — cite Aarsand AK et al., The EFLM Biological Variation Database | no; the installer takes the data from the API itself or obtains EFLM's permission |
| NCI Thesaurus (NCIt codes in CTCAE) | concept codes | CC BY 4.0 | yes, with attribution |
| OpenStreetMap (trails of the region pack) | only in the private region pack | ODbL | no (the region pack is private) |

## Third-party code and prompts

| Source | What is in the project | Terms | Goes into the publication |
|---|---|---|---|
| [WellAlly-health](https://github.com/huifer/WellAlly-health) (huifer / WellAlly Tech) | the specialist physician prompts (`specialists/*.md`, except `lifestyle_*` and `food_rule_generator.md`; `symptom_intake_system.txt` is our own) and the text of the coordinator prompt (`consultation-coordinator.md`). The design of the consilium — rounds, coordinator, choice of participants — is our own | MIT, text below; upstream updates are tracked by `check_wellally_updates.py` | yes, with this notice |

### WellAlly-health — MIT License

```
MIT License

Copyright (c) 2026 WellAlly Tech

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
